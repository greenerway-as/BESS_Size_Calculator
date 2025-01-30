import requests
import datetime
import matplotlib.pyplot as plt


# Function to fetch spot prices from hvakosterstrom API
def fetch_spot_prices(date, region):
    year, month, day = date.strftime('%Y'), date.strftime('%m'), date.strftime('%d')
    url = f'https://www.hvakosterstrommen.no/api/v1/prices/{year}/{month}-{day}_{region}.json'

    try:
        response = requests.get(url)
        response.raise_for_status()
        prices = response.json()
        return [entry["NOK_per_kWh"] for entry in prices]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching spot prices: {e}")
        return None


# Function to get hourly consumption from user
def get_consumption_profile():
    consumption = []
    print("\nEnter your 24-hour consumption profile in kWh (one value per hour):")
    for hour in range(24):
        value = float(input(f"Hour {hour}: "))
        consumption.append(value)
    return consumption


# Function to get battery and grid parameters
def get_user_parameters():
    grid_threshold = float(input("\nEnter your grid import threshold in kW: "))
    battery_efficiency = float(input("Enter your battery efficiency (in %): ")) / 100
    c_rating = float(input("Enter your battery C-rating: "))
    min_soc = float(input("Enter your minimum state of charge (in %): ")) / 100
    max_soc = float(input("Enter your maximum state of charge (in %): ")) / 100
    return grid_threshold, battery_efficiency, c_rating, min_soc, max_soc


# Function to determine optimal battery size
def determine_battery_size(consumption, grid_threshold):
    peak_exceedances = [max(0, consumption[hour] - grid_threshold) for hour in range(24)]
    return sum(peak_exceedances)  # Total energy needed for peak shaving (in kWh)


# Function to optimize BESS for peak shaving and arbitrage
def optimize_bess(consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc, battery_capacity):
    if battery_capacity == 0:
        return [0] * 24, [0] * 24, consumption  # No BESS optimization

    soc = max_soc * battery_capacity
    charge_schedule, discharge_schedule, net_grid_load = [0] * 24, [0] * 24, [0] * 24

    # Peak Shaving Optimization
    for hour in range(24):
        if consumption[hour] > grid_threshold:
            excess_load = consumption[hour] - grid_threshold
            discharge_power = min(excess_load, battery_capacity, soc * battery_efficiency)
            discharge_schedule[hour] = discharge_power
            soc -= discharge_power / battery_efficiency

    # Arbitrage Optimization
    sorted_hours = sorted(range(24), key=lambda x: spot_prices[x])  # Sort by cheapest prices
    for hour in sorted_hours:
        if soc < max_soc * battery_capacity and consumption[hour] <= grid_threshold:
            charge_power = min(battery_capacity, (max_soc * battery_capacity - soc) / battery_efficiency)
            charge_schedule[hour] = charge_power
            soc += charge_power * battery_efficiency

    # Compute net grid load after BESS operation
    for hour in range(24):
        net_grid_load[hour] = consumption[hour] - discharge_schedule[hour] + charge_schedule[hour]

    return charge_schedule, discharge_schedule, net_grid_load


# Function to calculate cost savings
def calculate_savings(consumption, spot_prices, net_grid_load):
    initial_cost = sum([consumption[hour] * spot_prices[hour] for hour in range(24)])
    optimized_cost = sum([net_grid_load[hour] * spot_prices[hour] for hour in range(24)])
    savings = initial_cost - optimized_cost
    return initial_cost, optimized_cost, savings


# Function to compare different BESS sizes
def compare_bess_sizes(consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc):
    bess_sizes = [0, 500, 1000, 1500]  # Different BESS capacities (kWh)
    results = {}

    print("\n🔍 Comparing Different BESS Sizes...\n")
    for size in bess_sizes:
        charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
            consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc, size
        )
        initial_cost, optimized_cost, savings = calculate_savings(consumption, spot_prices, net_grid_load)
        results[size] = (initial_cost, optimized_cost, savings)
        print(
            f"BESS Size: {size} kWh | Initial Cost: {initial_cost:.2f} NOK | Optimized Cost: {optimized_cost:.2f} NOK | Savings: {savings:.2f} NOK")

    best_size = max(results, key=lambda x: results[x][2])  # Select battery size with max savings
    print(f"\n✅ Best BESS Size: {best_size} kWh (Max Savings: {results[best_size][2]:.2f} NOK)")

    return best_size


# Function to plot results
def plot_results(consumption, spot_prices, charge_schedule, discharge_schedule, net_grid_load):
    hours = range(24)

    plt.figure(figsize=(12, 8))

    plt.subplot(3, 1, 1)
    plt.plot(hours, consumption, label='Original Consumption (kWh)', color='blue')
    plt.plot(hours, net_grid_load, label='Net Grid Load after BESS (kWh)', color='red')
    plt.xlabel('Hour')
    plt.ylabel('Energy (kWh)')
    plt.title('Energy Consumption & Net Grid Load')
    plt.legend()

    plt.subplot(3, 1, 2)
    plt.plot(hours, spot_prices, label='Spot Price (NOK/kWh)', color='orange')
    plt.xlabel('Hour')
    plt.ylabel('Price (NOK/kWh)')
    plt.title('Nordic Spot Prices (NO1)')
    plt.legend()

    plt.subplot(3, 1, 3)
    plt.plot(hours, charge_schedule, label='Charge Schedule (kW)', color='green')
    plt.plot(hours, discharge_schedule, label='Discharge Schedule (kW)', color='red')
    plt.xlabel('Hour')
    plt.ylabel('Power (kW)')
    plt.title('Battery Charge & Discharge Cycles')
    plt.legend()

    plt.tight_layout()
    plt.show()


# Main function
def main():
    today = datetime.date.today()
    spot_prices = fetch_spot_prices(today, "NO1")

    if not spot_prices:
        print("Failed to fetch spot prices. Exiting.")
        return

    consumption = get_consumption_profile()
    grid_threshold, battery_efficiency, c_rating, min_soc, max_soc = get_user_parameters()

    # Compare BESS sizes
    best_bess_size = compare_bess_sizes(consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc)

    # Optimize with best BESS size
    charge_schedule, discharge_schedule, net_grid_load = optimize_bess(
        consumption, spot_prices, grid_threshold, battery_efficiency, min_soc, max_soc, best_bess_size
    )

    # Calculate final savings
    initial_cost, optimized_cost, savings = calculate_savings(consumption, spot_prices, net_grid_load)
    print(f"\n🔹 Final Cost Savings: {savings:.2f} NOK")

    # Plot results
    plot_results(consumption, spot_prices, charge_schedule, discharge_schedule, net_grid_load)


# Run script
if __name__ == "__main__":
    main()
