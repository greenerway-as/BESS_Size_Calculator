import streamlit as st
import requests
import datetime
import matplotlib.pyplot as plt
import pandas as pd


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


def get_consumption_profile():
    consumption = []
    st.write("Enter your 24-hour consumption profile in kWh (one value per hour):")
    for hour in range(24):
        value = st.number_input(f"Hour {hour}", min_value=0.0, step=0.1)
        consumption.append(value)
    return consumption


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


def optimize_bess(consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency,
                  min_soc, max_soc):
    if battery_capacity == 0:
        return [0] * 24, [0] * 24, consumption

    soc = max_soc * battery_capacity
    charge_schedule, discharge_schedule, net_grid_load = [0] * 24, [0] * 24, consumption[:]

    # Prioritize peak shaving
    for hour in range(24):
        if net_grid_load[hour] > grid_threshold:
            excess_load = net_grid_load[hour] - grid_threshold
            discharge_power = min(excess_load, battery_power, soc * battery_efficiency)
            discharge_schedule[hour] = discharge_power
            soc -= discharge_power / battery_efficiency
            net_grid_load[hour] -= discharge_power
            net_grid_load[hour] = min(net_grid_load[hour], grid_threshold)

    return charge_schedule, discharge_schedule, net_grid_load

def compute_peak_shaving_savings(consumption, grid_threshold):
    highest_hourly_consumption = max(consumption)
    peak_shaving = max(0, highest_hourly_consumption - grid_threshold)
    total_savings = peak_shaving * 104 * 6
    return peak_shaving, total_savings

def price_arbitrage_schedule(spot_prices, battery_power, battery_capacity, battery_efficiency):
    soc = 0
    charge_schedule = [0] * 24
    discharge_schedule = [0] * 24
    arbitrage_savings = 0

    lowest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x])[:3]
    highest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x], reverse=True)[:3]

    for hour in lowest_prices_indices:
        if soc < battery_capacity:
            charge_power = min(battery_power, (battery_capacity - soc) / battery_efficiency)
            charge_schedule[hour] = charge_power
            soc += charge_power * battery_efficiency

    for hour in highest_prices_indices:
        if soc > 0:
            discharge_power = round(min(battery_power, soc * battery_efficiency),2)
            discharge_schedule[hour] = discharge_power
            soc -= discharge_power / battery_efficiency

    for hour in range(24):
        arbitrage_savings += discharge_schedule[hour] * spot_prices[hour] - charge_schedule[hour] * spot_prices[hour]

    return charge_schedule, discharge_schedule, arbitrage_savings


def plot_results(consumption, spot_prices, net_grid_load, grid_threshold):
    hours = range(24)

    fig, ax = plt.subplots(2, 1, figsize=(12, 10))

    # Plot 1: Energy Consumption & Net Grid Load
    ax[0].bar(hours, consumption, label='Original Consumption (kWh)', color='blue', alpha=0.6)
    ax[0].bar(hours, net_grid_load, label='Net Grid Load after BESS (kWh)', color='red', alpha=0.6)
    ax[0].axhline(y=grid_threshold, color='green', linestyle='--', label='Grid Threshold (kW)')

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

    # Optimize BESS for peak shaving
    charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
        consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc
    )

    # Compute peak shaving savings
    peak_shaving, total_savings = compute_peak_shaving_savings(consumption, grid_threshold)

    st.subheader("Peak Shaving Analysis")
    st.write(f"Highest Hourly Consumption: {max(consumption):.2f} kWh")
    st.write(f"Peak Shaving for the Day: {peak_shaving:.2f} kWh")
    st.write(f"Total Savings from Peak Shaving: {total_savings:.2f} NOK")

    # Price Arbitrage Optimization
    charge_schedule, discharge_schedule, arbitrage_savings = price_arbitrage_schedule(
        spot_prices, battery_power, battery_capacity, battery_efficiency
    )

    st.subheader("Price Arbitrage Optimization")
    st.write(
        "**Assumption: The battery starts at 0% SoC at the beginning of the day and can discharge to 0% at the end.**")
    st.write(f"Total Savings from Price Arbitrage: {arbitrage_savings:.2f} NOK")
    st.write(f"Charging schedule: {charge_schedule}")
    st.write(f"Discharging schedule: {discharge_schedule}")

    plot_results(consumption, spot_prices, net_grid_load, grid_threshold)

if __name__ == "__main__":
    main()

