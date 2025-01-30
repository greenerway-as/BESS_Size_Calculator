import streamlit as st
import requests
import datetime
import matplotlib.pyplot as plt
import numpy as np


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
    battery_efficiency = st.number_input("Enter your battery efficiency (in %):", min_value=0.0, max_value=100.0,
                                         step=0.1) / 100
    c_rating = st.number_input("Enter your battery C-rating:", min_value=0.0, step=0.1)
    min_soc = st.number_input("Enter your minimum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1) / 100
    max_soc = st.number_input("Enter your maximum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1) / 100
    return grid_threshold, battery_efficiency, c_rating, min_soc, max_soc


# Determine battery size needed for peak shaving
def determine_battery_size(consumption, grid_threshold):
    peak_exceedances = [max(0, consumption[hour] - grid_threshold) for hour in range(24)]
    return sum(peak_exceedances)


# Function to optimize BESS for peak shaving and arbitrage
def optimize_bess(consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc, battery_capacity):
    if battery_capacity == 0:
        return [0] * 24, [0] * 24, consumption

    # Ensure battery efficiency is not zero
    if battery_efficiency == 0:
        st.error("Battery efficiency cannot be zero. Please enter a valid efficiency value.")
        return [0] * 24, [0] * 24, consumption

    soc = max_soc * battery_capacity
    charge_schedule, discharge_schedule, net_grid_load = [0] * 24, [0] * 24, [0] * 24

    for hour in range(24):
        if consumption[hour] > grid_threshold:
            excess_load = consumption[hour] - grid_threshold
            discharge_power = min(excess_load, battery_capacity, soc * battery_efficiency)
            discharge_schedule[hour] = discharge_power
            soc -= discharge_power / battery_efficiency  # Avoid ZeroDivisionError

    sorted_hours = sorted(range(24), key=lambda x: spot_prices[x])
    for hour in sorted_hours:
        if soc < max_soc * battery_capacity and consumption[hour] <= grid_threshold:
            charge_power = min(battery_capacity, (max_soc * battery_capacity - soc) / battery_efficiency)
            charge_schedule[hour] = charge_power
            soc += charge_power * battery_efficiency

    for hour in range(24):
        net_grid_load[hour] = consumption[hour] - discharge_schedule[hour] + charge_schedule[hour]

    return charge_schedule, discharge_schedule, net_grid_load


# Calculate cost savings
def calculate_savings(consumption, spot_prices, net_grid_load):
    initial_cost = sum([consumption[hour] * spot_prices[hour] for hour in range(24)])
    optimized_cost = sum([net_grid_load[hour] * spot_prices[hour] for hour in range(24)])
    savings = initial_cost - optimized_cost
    return initial_cost, optimized_cost, savings


# Plot results using Streamlit
def plot_results(consumption, spot_prices, charge_schedule, discharge_schedule, net_grid_load):
    hours = range(24)

    fig, ax = plt.subplots(3, 1, figsize=(10, 12))

    # Energy Consumption & Net Grid Load
    ax[0].plot(hours, consumption, label='Original Consumption (kWh)', color='blue')
    ax[0].plot(hours, net_grid_load, label='Net Grid Load after BESS (kWh)', color='red')
    ax[0].set_title('Energy Consumption & Net Grid Load')
    ax[0].set_xlabel('Hour')
    ax[0].set_ylabel('Energy (kWh)')
    ax[0].legend()

    # Spot Prices
    ax[1].plot(hours, spot_prices, label='Spot Price (NOK/kWh)', color='orange')
    ax[1].set_title('Nordic Spot Prices (NO1)')
    ax[1].set_xlabel('Hour')
    ax[1].set_ylabel('Price (NOK/kWh)')
    ax[1].legend()

    # Charge and Discharge Schedules
    ax[2].plot(hours, charge_schedule, label='Charge Schedule (kW)', color='green')
    ax[2].plot(hours, discharge_schedule, label='Discharge Schedule (kW)', color='red')
    ax[2].set_title('Battery Charge & Discharge Cycles')
    ax[2].set_xlabel('Hour')
    ax[2].set_ylabel('Power (kW)')
    ax[2].legend()

    plt.tight_layout()
    st.pyplot(fig)


# Main function for Streamlit
def main():
    today = datetime.date.today()
    region = "NO1"
    spot_prices = fetch_spot_prices(today, region)

    if not spot_prices:
        st.error("Failed to fetch spot prices. Exiting.")
        return

    # User inputs
    st.title("Battery Energy Storage System (BESS) Optimization")
    consumption = get_consumption_profile()
    grid_threshold, battery_efficiency, c_rating, min_soc, max_soc = get_user_parameters()

    # Optimize BESS
    battery_capacity = determine_battery_size(consumption, grid_threshold)
    charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
        consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc, battery_capacity
    )

    # Calculate savings
    initial_cost, optimized_cost, savings = calculate_savings(consumption, spot_prices, net_grid_load)
    st.write(f"🔹 Initial Cost (without BESS optimization): {initial_cost:.2f} NOK")
    st.write(f"🔹 Optimized Cost (with BESS optimization): {optimized_cost:.2f} NOK")
    st.write(f"🔹 Total Savings: {savings:.2f} NOK")

    # Plot results
    plot_results(consumption, spot_prices, charge_schedule, discharge_schedule, net_grid_load)


if __name__ == "__main__":
    main()
