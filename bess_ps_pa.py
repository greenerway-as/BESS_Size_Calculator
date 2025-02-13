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

def get_user_parameters(highest_hourly_consumption):
    battery_power_options = list(range(100, 2001, 100))
    battery_power = st.selectbox("Select Battery Power (kW):", battery_power_options)

    min_grid_threshold = float(highest_hourly_consumption - battery_power)
    if min_grid_threshold < 0:
        min_grid_threshold = 0.0

    grid_threshold = st.number_input(
        "Enter your grid import threshold in kW:",
        min_value=min_grid_threshold,
        step=0.1,
        value=min_grid_threshold
    )

    if grid_threshold < min_grid_threshold:
        st.error(f"Minimum grid threshold should be {min_grid_threshold:.2f} kW (Highest Hourly Consumption - Battery Power).")
        grid_threshold = min_grid_threshold

    c_rate_options = [0.5, 1.0]
    c_rate = st.selectbox("Select C-Rate:", c_rate_options)

    if c_rate == 1:
        battery_capacity = battery_power
    else:
        battery_capacity = 2.15 * battery_power

    st.write(f"Battery Capacity: {battery_capacity:.2f} kWh")

    battery_efficiency = st.number_input("Enter your battery efficiency (in %):", min_value=50.0, max_value=100.0,
                                         step=0.1, value=90.0) / 100
    min_soc = st.number_input("Enter your minimum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1, value=10.0) / 100
    max_soc = st.number_input("Enter your maximum state of charge (in %):", min_value=0.0, max_value=100.0,
                              step=0.1, value=90.0) / 100
    return grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc

def optimize_bess(consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency,
                  min_soc, max_soc, initial_soc=None):


    charge_schedule = [0] * 24
    discharge_schedule = [0] * 24
    net_grid_load = consumption[:]  # Create a copy to modify
    arbitrage_savings = 0

    # Find the indices of the 3 lowest and 3 highest spot prices
    lowest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x])[:3]
    highest_prices_indices = sorted(range(24), key=lambda x: spot_prices[x], reverse=True)[:3]

    # Charging logic
    for hour in lowest_prices_indices:
        charge_schedule[hour] = battery_power # Charge at full battery power
        net_grid_load[hour] += battery_power # Increase grid load due to charging
        arbitrage_savings -= battery_power * spot_prices[hour] # Reduce savings as we buy power

    # Discharging logic
    for hour in highest_prices_indices:
        discharge_schedule[hour] = battery_power # Discharge at full battery power
        net_grid_load[hour] -= battery_power # Reduce grid load due to discharging
        arbitrage_savings += battery_power * spot_prices[hour] # Increase savings as we sell power

    return charge_schedule, discharge_schedule, net_grid_load, arbitrage_savings

def compute_peak_shaving_savings(consumption, grid_threshold):
    highest_hourly_consumption = max(consumption)
    peak_shaving = max(0, highest_hourly_consumption - grid_threshold)
    total_savings = peak_shaving * 104 * 6
    return peak_shaving, total_savings

def plot_results(consumption, spot_prices, net_grid_load, grid_threshold):
    hours = range(24)

    fig, ax = plt.subplots(2, 1, figsize=(12, 10))
    ax[0].bar(hours, consumption, label='Original Consumption (kWh)', color='blue', alpha=0.6)
    ax[0].bar(hours, net_grid_load, label='Net Grid Load after using BESS (kWh)', color='red', alpha=0.6)
    ax[0].axhline(y=grid_threshold, color='green', linestyle='--', label='Grid Threshold (kW)')

    ax[0].set_title('Energy Consumption & Net Grid Load')
    ax[0].set_xlabel('Hour')
    ax[0].set_ylabel('Energy (kWh)')
    ax[0].legend()

    ax[1].plot(hours, spot_prices, label='Spot Price (NOK/kWh)', color='orange')
    ax[1].set_title('Nordic Spot Prices (NO1)')
    ax[1].set_xlabel('Hour')
    ax[1].set_ylabel('Price (NOK/kWh)')
    ax[1].legend()

    plt.tight_layout()
    st.pyplot(fig)

def fetch_battery_soc(site_id, api_url, api_username, api_password):
    try:
        url = api_url.format(site_id=site_id)
        response = requests.get(url, auth=(api_username, api_password))
        response.raise_for_status()
        data = response.json()
        battery_soc = data.get('batterySoc')
        if battery_soc is not None:
            return battery_soc
        else:
            st.warning("Battery SoC data not available.")
            return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching battery SOC: {e}")
        return None
    except ValueError:
        st.error("Response content is not in JSON format.")
        return None

def main():
    today = datetime.date.today()
    region = "NO1"

    st.title("BESS Size Calculator")
    st.sidebar.header("User Inputs")
    data_source = st.sidebar.radio("Choose data entry method:", ("Manual Entry", "Upload CSV"))

    # Date Selection for Monthly Data
    start_date = st.sidebar.date_input("Start Date for Monthly Data", today - datetime.timedelta(days=30))
    end_date = st.sidebar.date_input("End Date for Monthly Data", today)

    date_range = [start_date + datetime.timedelta(days=x) for x in range((end_date - start_date).days + 1)]

    monthly_hourly_consumption = {}
    average_top_3_consumption = 0  # Initialize it here

    consumption = []

    if data_source == "Manual Entry":
        consumption = get_consumption_profile()
    else:
        uploaded_file = st.sidebar.file_uploader("Upload CSV", type=["csv"])
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file, sep=";", encoding="utf-8-sig", parse_dates=["Fra"], dayfirst=True)
                df.loc[:, "Hour"] = df["Fra"].dt.hour
                df['Date'] = df["Fra"].dt.date

                # Aggregate data for the specified date range
                df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]

                # Ensure 'KWH 15 Forbruk' column exists
                if 'KWH 15 Forbruk' not in df.columns:
                    st.error("The column 'KWH 15 Forbruk' is not found in the CSV file.")
                    return

                # Group by Date and Hour
                grouped = df.groupby(['Date', 'Hour'])['KWH 15 Forbruk'].apply(
                    lambda x: sum(map(float, x.str.replace(",", ".")))
                ).reset_index()

                # Convert consumption to numeric, handling potential errors
                grouped['KWH 15 Forbruk'] = pd.to_numeric(grouped['KWH 15 Forbruk'], errors='coerce')
                grouped.dropna(subset=['KWH 15 Forbruk'], inplace=True)

                # Aggregate consumption by date and hour
                for date in grouped['Date'].unique():
                    date_data = grouped[grouped['Date'] == date]
                    hourly_consumption = date_data.groupby('Hour')['KWH 15 Forbruk'].sum().to_dict()
                    monthly_hourly_consumption[date] = hourly_consumption

                # Find Top 3 Consumption Hours Across All Dates
                all_consumption_data = []
                for date, hourly_data in monthly_hourly_consumption.items():
                    for hour, consumption_value in hourly_data.items():
                        all_consumption_data.append((date, hour, consumption_value))

                # Sort by consumption value
                top_3_consumption = sorted(all_consumption_data, key=lambda x: x[2], reverse=True)[:3]

                st.write("Top 3 Hours with Highest Consumption:")
                for date, hour, consumption_value in top_3_consumption:
                    st.write(f"Date: {date}, Hour: {hour}, Consumption: {consumption_value:.2f} kWh")

                # Use the date with the highest hourly consumption
                date_with_highest_consumption = top_3_consumption[0][0]
                hourly_consumption_highest_date = \
                    grouped[grouped['Date'] == date_with_highest_consumption].groupby('Hour')[
                        'KWH 15 Forbruk'].sum().tolist()
                consumption = [round(value, 2) for value in hourly_consumption_highest_date]

                st.write(f"Data for {date_with_highest_consumption} (highest consumption date) loaded successfully!")
                st.write("Hourly consumption:")
                st.write(consumption)
                average_top_3_consumption = sum(x[2] for x in top_3_consumption) / len(
                    top_3_consumption) if top_3_consumption else 0

            except Exception as e:
                st.error(f"Error processing the uploaded CSV file: {e}")
                consumption = []
        else:
            st.warning("Please upload a CSV file to proceed.")

    if not consumption:
        st.error("Consumption data is required to proceed.")
        return

    # Calculate highest hourly consumption
    highest_hourly_consumption = max(consumption)

    grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc = get_user_parameters(highest_hourly_consumption)

    site_id = st.sidebar.text_input("Enter Site ID:")
    api_url = "https://ems.greenerway.services/api/v1/sites/{site_id}/measurements/realtime"
    api_username = "batteri"
    api_password = "batteri"

    initial_soc = None
    if site_id:
        fetched_soc = fetch_battery_soc(site_id, api_url, api_username, api_password)
        if fetched_soc is not None:
            initial_soc = fetched_soc / 100
            st.write(f"Fetched initial Battery SOC from API: {initial_soc:.2f}")
        else:
            st.warning("Failed to fetch initial Battery SOC from API. Using default max_soc.")
            initial_soc = max_soc
    else:
        initial_soc = max_soc

    # Store daily results
    daily_results = {}
    total_arbitrage_savings = 0
    daily_socs = {}
    current_soc = initial_soc  # Initialize the initial SOC

    # Process each day in the selected date range
    for current_date in date_range:
        spot_prices = fetch_spot_prices(current_date, region)
        if not spot_prices:
            st.warning(f"Failed to fetch spot prices for {current_date}. Skipping.")
            continue

        # Call the optimize_bess function for each day
        charge_schedule, discharge_schedule, net_grid_load, arbitrage_savings = optimize_bess(
            consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc,
            max_soc, current_soc
        )

        daily_results[current_date] = (charge_schedule, discharge_schedule, arbitrage_savings)
        total_arbitrage_savings += arbitrage_savings

        # Update initial_soc for the next day based on today's schedules
        final_soc = current_soc * battery_capacity  # Convert SOC fraction to kWh
        for hour in range(24):
            final_soc += charge_schedule[hour] * battery_efficiency - discharge_schedule[hour] / battery_efficiency

        final_soc = max(min_soc * battery_capacity, min(final_soc, max_soc * battery_capacity))
        current_soc = final_soc / battery_capacity


    peak_shaving, total_savings = compute_peak_shaving_savings(consumption, grid_threshold)

    st.subheader("Peak Shaving Analysis")
    st.write(f"Highest Hourly Consumption: {highest_hourly_consumption:.2f} kWh")
    st.write(f"Average of Top 3 Hours with Highest Consumptions: {average_top_3_consumption:.2f} kWh")
    st.write(f"Peak Shaving considering the Day with highest hourly consumption: {peak_shaving:.2f} kWh")
    st.write(f"Total Savings from Peak Shaving for 6 months(winter): {total_savings:.2f} NOK")

    st.subheader("Monthly Price Arbitrage Optimization")
    st.write(f"Total Savings from Price Arbitrage for the month: {total_arbitrage_savings:.2f} NOK")

    # Date Selection using Selectbox
    date_options = [date.strftime('%Y-%m-%d') for date in date_range]
    selected_date_str = st.selectbox("Select a date to view the charge/discharge schedule", date_options)
    selected_date = datetime.datetime.strptime(selected_date_str, '%Y-%m-%d').date() #Convert the string back to date

    if selected_date:
        if selected_date in daily_results:
            charge_schedule, discharge_schedule, daily_arbitrage_savings = daily_results[selected_date]

            # Create a DataFrame for the schedule
            schedule_data = {'Hour': range(24),
                             'Charge (kWh)': charge_schedule,
                             'Discharge (kWh)': discharge_schedule}
            schedule_df = pd.DataFrame(schedule_data)

            #Add idle state
            schedule_df['State'] = 'Idle'
            schedule_df.loc[schedule_df['Charge (kWh)'] > 0, 'State'] = 'Charging'
            schedule_df.loc[schedule_df['Discharge (kWh)'] > 0, 'State'] = 'Discharging'

            st.subheader(f"Charge/Discharge Schedule for {selected_date}")
            st.dataframe(schedule_df)

            st.write(f"Daily Arbitrage Savings: {daily_arbitrage_savings:.2f} NOK")
        else:
            st.write("No data available for the selected date.")

        #plot_results(consumption, spot_prices, net_grid_load, grid_threshold)

if __name__ == "__main__":
    main()
