import streamlit as st
import requests
import datetime
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# Fetch spot prices from hvakosterstrom API
def fetch_spot_prices(date, region):
    year, month, day = date.strftime('%Y'), date.strftime('%m'), date.strftime('%d')
    url = f'https://www.hvakosterstrommen.no/api/v1/prices/{year}/{month}-{day}_{region}.json'

    try:
        response = requests.get(url)
        response.raise_for_status()
        prices = response.json()
        return [entry["NOK_per_kWh"] for entry in prices]
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching spot prices: {e}")
        return None


# Get consumption profile from user
def get_consumption_profile():
    consumption = []
    st.write("Enter your 24-hour consumption profile in kWh (one value per hour):")
    for hour in range(24):
        value = st.number_input(f"Hour {hour}", min_value=0.0, step=0.1)
        consumption.append(value)
    return consumption


# Get battery and grid parameters from user
def get_user_parameters():
    grid_threshold = st.number_input("Enter your grid import threshold in kW:", min_value=0.0, step=0.1)
    battery_power = st.number_input("Enter your battery power (in kW):", min_value=0.0, step=0.1)
    battery_capacity = st.number_input("Enter your battery capacity (in kWh):", min_value=0.0, step=0.1)
    battery_efficiency = st.number_input("Enter your battery efficiency (in %):", min_value=50.0, max_value=100.0,
                                         step=0.1) / 100
    min_soc = st.number_input("Enter your minimum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1) / 100
    max_soc = st.number_input("Enter your maximum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1) / 100
    return grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc


# Determine battery size needed for peak shaving
def determine_battery_size(consumption, grid_threshold):
    peak_exceedances = [max(0, consumption[hour] - grid_threshold) for hour in range(24)]
    return sum(peak_exceedances)


def optimize_bess(consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc):
    if battery_capacity == 0:
        return [0] * 24, [0] * 24, consumption

    soc = max_soc * battery_capacity
    charge_schedule, discharge_schedule, net_grid_load = [0] * 24, [0] * 24, consumption[:]

    # First pass: Prioritize peak shaving
    for hour in range(24):
        if net_grid_load[hour] > grid_threshold:
            excess_load = net_grid_load[hour] - grid_threshold
            discharge_power = min(excess_load, battery_power, soc * battery_efficiency)
            discharge_schedule[hour] = discharge_power
            soc -= discharge_power / battery_efficiency
            net_grid_load[hour] -= discharge_power

            # Ensure net grid load does not exceed threshold
            net_grid_load[hour] = min(net_grid_load[hour], grid_threshold)

    # Second pass: Arbitrage while maintaining grid import and preventing negative net load
    lowest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x])[:3]
    highest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x], reverse=True)[:3]

    for hour in lowest_prices_indices:
        if soc < max_soc * battery_capacity:
            charge_power = min(battery_power, (max_soc * battery_capacity - soc) / battery_efficiency)
            if net_grid_load[hour] + charge_power <= grid_threshold:
                charge_schedule[hour] = charge_power
                soc += charge_power * battery_efficiency
                net_grid_load[hour] += charge_power

    for hour in highest_prices_indices:
        if soc > min_soc * battery_capacity:
            discharge_power = min(battery_power, soc * battery_efficiency)
            potential_net_load = net_grid_load[hour] - discharge_power
            if potential_net_load >= 0:
                discharge_schedule[hour] += discharge_power
                soc -= discharge_power / battery_efficiency
                net_grid_load[hour] -= discharge_power

    # Final pass: Strictly enforce grid threshold and non-negative net load
    for hour in range(24):
        if net_grid_load[hour] > grid_threshold:
            excess = net_grid_load[hour] - grid_threshold
            discharge_power = min(excess, battery_power, soc * battery_efficiency)
            discharge_schedule[hour] += discharge_power
            soc -= discharge_power / battery_efficiency
            net_grid_load[hour] -= discharge_power
            net_grid_load[hour] = min(net_grid_load[hour], grid_threshold)

        if net_grid_load[hour] < 0:
            charge_power = min(-net_grid_load[hour], battery_power, (max_soc * battery_capacity - soc) / battery_efficiency)
            charge_schedule[hour] += charge_power
            soc += charge_power * battery_efficiency
            net_grid_load[hour] += charge_power
            net_grid_load[hour] = max(net_grid_load[hour], 0)

    return charge_schedule, discharge_schedule, net_grid_load


# Function to compare different BESS sizes
def compare_bess_sizes(consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency,
                       min_soc, max_soc):
    bess_sizes = [0, 500, 1000, 1500, 2000]
    results = {}

    for size in bess_sizes:
        charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
            consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc,
            max_soc
        )
        initial_cost, optimized_cost, savings = calculate_savings(consumption, spot_prices, net_grid_load)
        results[size] = {"Initial Cost (NOK)": initial_cost,
                         "Optimized Cost (NOK)": optimized_cost,
                         "Savings (NOK)": savings}

    best_size = max(results, key=lambda x: results[x]["Savings (NOK)"])
    return results, best_size


# Calculate cost savings
def calculate_savings(consumption, spot_prices, net_grid_load):
    initial_cost = sum([consumption[hour] * spot_prices[hour] for hour in range(24)])
    optimized_cost = sum([net_grid_load[hour] * spot_prices[hour] for hour in range(24)])
    savings = initial_cost - optimized_cost
    return initial_cost, optimized_cost, savings


def plot_results(consumption, spot_prices, net_grid_load, grid_threshold):
    hours = range(24)

    fig, ax = plt.subplots(2, 1, figsize=(12, 10))

    # Plot 1: Energy Consumption & Net Grid Load
    ax[0].bar(hours, consumption, label='Original Consumption (kWh)', color='blue', alpha=0.6)
    ax[0].bar(hours, net_grid_load, label='Net Grid Load after BESS (kWh)', color='red', alpha=0.6)
    ax[0].axhline(y=grid_threshold, color='green', linestyle='--', label='Grid Threshold (kW)')

    # Highlight where net load exceeds the grid threshold
    for hour in range(24):
        if net_grid_load[hour] > grid_threshold:
            ax[0].annotate('Exceeds Threshold', xy=(hour, net_grid_load[hour]),
                           xytext=(hour, net_grid_load[hour] + 0.5),
                           arrowprops=dict(facecolor='black', shrink=0.05), fontsize=8, color='darkred')

    ax[0].set_title('Energy Consumption & Net Grid Load')
    ax[0].set_xlabel('Hour')
    ax[0].set_ylabel('Energy (kWh)')
    ax[0].legend()

    # Plot 2: Spot Prices
    ax[1].plot(hours, spot_prices, label='Spot Price (NOK/kWh)', color='orange')
    ax[1].set_title('Nordic Spot Prices (NO1)')
    ax[1].set_xlabel('Hour')
    ax[1].set_ylabel('Price (NOK/kWh)')
    ax[1].legend()

    plt.tight_layout()
    st.pyplot(fig)


def main():
    today = datetime.date.today()
    region = "NO1"
    spot_prices = fetch_spot_prices(today, region)

    if not spot_prices:
        st.error("Failed to fetch spot prices. Exiting.")
        return

    st.title("BESS Size Calculator")

    # Move user inputs to the sidebar
    st.sidebar.header("User Inputs")
    data_source = st.sidebar.radio("Choose data entry method:", ("Manual Entry", "Upload CSV"))

    if data_source == "Manual Entry":
        consumption = get_consumption_profile()
    else:
        uploaded_file = st.sidebar.file_uploader("Upload CSV", type=["csv"])
        consumption = []

        if uploaded_file is not None:
            df = pd.read_csv(uploaded_file, sep=";", encoding="utf-8-sig", parse_dates=["Fra"], dayfirst=True)
            df.loc[:, "Hour"] = df["Fra"].dt.hour  # Fixing SettingWithCopyWarning
            df['Date'] = df['Fra'].dt.date
            unique_dates = df['Date'].unique()
            date_choice = st.sidebar.selectbox("Select the date to analyze", unique_dates)
            df_selected = df[df['Date'] == date_choice]
            df_selected["Hour"] = df_selected["Fra"].dt.hour
            hourly_consumption = df_selected.groupby("Hour")["KWH 15 Forbruk"].apply(
                lambda x: sum(map(float, x.str.replace(",", ".")))).tolist()
            hourly_consumption = [round(value, 2) for value in hourly_consumption]
            st.write(f"Data for {date_choice} loaded successfully!")
            st.write("Hourly consumption:")
            st.write(hourly_consumption)
            consumption = hourly_consumption
        else:
            st.warning("Please upload a CSV file to proceed.")

    if not consumption:
        st.error("Consumption data is required to proceed.")
        return

    grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc = get_user_parameters()

    battery_capacity = determine_battery_size(consumption, grid_threshold)
    charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
        consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc
    )

    initial_cost, optimized_cost, savings = calculate_savings(consumption, spot_prices, net_grid_load)
    st.write(f"🔹 Initial Cost (without BESS optimization): {initial_cost:.2f} NOK")
    st.write(f"🔹 Optimized Cost (with BESS optimization): {optimized_cost:.2f} NOK")
    st.write(f"🔹 Total Savings: {savings:.2f} NOK")

    bess_comparison, best_size = compare_bess_sizes(consumption, spot_prices, grid_threshold, battery_power,
                                                    battery_capacity, battery_efficiency, min_soc, max_soc)

    st.subheader("Comparison of Different BESS Sizes")
    st.write("The table below compares cost savings for different battery sizes.")
    df_comparison = pd.DataFrame.from_dict(bess_comparison, orient='index')
    df_comparison.index.name = "BESS Size (kWh)"
    st.table(df_comparison)

    st.success(
        f"Recommended Battery Size: **{best_size} kWh** (Maximum Savings: {bess_comparison[best_size]['Savings (NOK)']:.2f} NOK)")

    st.subheader("Suggested BESS Charge and Discharge Schedule")
    schedule = ["Idle"] * 24

    for hour in range(24):
        if charge_schedule[hour] > 0:
            schedule[hour] = "Charge"
        elif discharge_schedule[hour] > 0:
            schedule[hour] = "Discharge"

    st.write("BESS Suggested Schedule (Hour: Action)")
    for hour in range(24):
        if schedule[hour] != "Idle":
            st.write(f"{hour}:00 - {schedule[hour]}")

    plot_results(consumption, spot_prices, net_grid_load, grid_threshold)


if __name__ == "__main__":
    main()