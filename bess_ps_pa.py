import datetime

import altair as alt
import pandas as pd
import requests
import streamlit as st

@st.cache_data
def fetch_spot_prices(date, region):
    year, month, day = date.strftime('%Y'), date.strftime('%m'), date.strftime('%d')
    url = f'https://www.hvakosterstrommen.no/api/v1/prices/{year}/{month}-{day}_{region}.json'
    try:
        response = requests.get(url)
        response.raise_for_status()
        prices = response.json()
        # Check if prices is valid and not empty
        if not isinstance(prices, list) or not prices:
            st.warning(f"No spot prices found for {date}.")
            return None
        spot_prices = [entry["NOK_per_kWh"] for entry in prices]
        if len(spot_prices) != 24:
            st.warning(f"Spot prices for {date} does not contain 24 entries.")
            return None
        return spot_prices
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching spot prices: {e}")
        return None
    except ValueError as e:
        st.error(f"Decoding JSON has failed: {e}")
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

    # Check if spot_prices has enough data
    if len(spot_prices) < 3:
        st.warning("Not enough spot price data to perform optimization.")
        return charge_schedule, discharge_schedule, net_grid_load, 0  # Return 0 arbitrage savings

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

def optimize_combined_peak_arbitrage(consumption, spot_prices, grid_threshold, battery_power,
                                     battery_capacity, battery_efficiency, min_soc, max_soc, initial_soc):
    soc = initial_soc * battery_capacity
    charge_schedule = [0] * 24
    discharge_schedule = [0] * 24
    net_grid_load = consumption.copy()
    arbitrage_savings = 0

    # Step 1: Identify peak hours and total energy needed for peak shaving
    peak_hours = sorted([h for h, cons in enumerate(consumption) if cons > grid_threshold])
    peak_shaving_requirement = {h: consumption[h] - grid_threshold for h in peak_hours}
    total_peak_shaving_needed = sum(peak_shaving_requirement.values()) / battery_efficiency

    # Step 2: Charge before peaks to ensure enough capacity for peak shaving
    non_peak_hours = [(h, spot_prices[h]) for h in range(24) if h not in peak_hours]
    non_peak_hours.sort(key=lambda x: x[1])  # Sort by lowest price first

    for hour, price in non_peak_hours:
        if soc < max_soc * battery_capacity:
            charge_needed = min(
                battery_power,
                (max_soc * battery_capacity - soc) / battery_efficiency
            )
            charge_schedule[hour] = charge_needed
            net_grid_load[hour] += charge_needed
            soc += charge_needed * battery_efficiency
            arbitrage_savings -= charge_needed * price

        if soc >= max_soc * battery_capacity:
            break

            # Step 3: Discharge at peak hours to ensure net_grid_load ≤ grid_threshold
    for hour in peak_hours:
        discharge_needed = peak_shaving_requirement[hour]
        discharge_possible = min(discharge_needed, (soc - min_soc * battery_capacity) * battery_efficiency, battery_power)

        if discharge_possible > 0:
            discharge_schedule[hour] = discharge_possible
            net_grid_load[hour] -= discharge_possible
            soc -= discharge_possible / battery_efficiency
            arbitrage_savings += discharge_possible * spot_prices[hour]

    # Step 4: Arbitrage with remaining capacity
    remaining_hours = [(h, spot_prices[h]) for h in range(24) if charge_schedule[h] == 0 and discharge_schedule[h] == 0]
    remaining_hours.sort(key=lambda x: x[1])  # Sort by price

    for hour, price in remaining_hours:
        if price < sum(spot_prices) / len(spot_prices):  # Charge if price is below average
            charge_possible = min(
                battery_power,
                (max_soc * battery_capacity - soc) / battery_efficiency,
                grid_threshold - net_grid_load[hour]
            )
            if charge_possible > 0:
                charge_schedule[hour] = charge_possible
                net_grid_load[hour] += charge_possible
                soc += charge_possible * battery_efficiency
                arbitrage_savings -= charge_possible * price
        else:  # Discharge if price is above average
            discharge_possible = min(
                battery_power,
                (soc - min_soc * battery_capacity) * battery_efficiency,
                net_grid_load[hour] - min(net_grid_load[hour], grid_threshold)
            )
            if discharge_possible > 0:
                discharge_schedule[hour] = discharge_possible
                net_grid_load[hour] -= discharge_possible
                soc -= discharge_possible / battery_efficiency
                arbitrage_savings += discharge_possible * price

    # Final check to ensure all hours are below grid threshold
    for hour in range(24):
        if net_grid_load[hour] > grid_threshold:
            excess = net_grid_load[hour] - grid_threshold
            if soc > min_soc * battery_capacity:
                discharge_possible = min(excess, (soc - min_soc * battery_capacity) * battery_efficiency, battery_power)
                discharge_schedule[hour] += discharge_possible
                net_grid_load[hour] -= discharge_possible
                soc -= discharge_possible / battery_efficiency
                arbitrage_savings += discharge_possible * spot_prices[hour]

    return charge_schedule, discharge_schedule, net_grid_load, arbitrage_savings, soc / battery_capacity

def main():
    today = datetime.date.today()
    region = "NO1"

    st.title("BESS Size Calculator")
    st.sidebar.header("User  Inputs")
    data_source = st.sidebar.radio("Choose data entry method:", ("Manual Entry", "Upload CSV"))
    start_date = st.sidebar.date_input("Start Date for Monthly Data", today - datetime.timedelta(days=30))
    end_date = st.sidebar.date_input("End Date for Monthly Data", today)

    date_range = [start_date + datetime.timedelta(days=x) for x in range((end_date - start_date).days + 1)]

    monthly_hourly_consumption = {}
    average_top_3_consumption = 0

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

                df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]

                # Determine if 'KWH 60 Forbruk' or 'KWH 15 Forbruk' is present
                if 'KWH 60 Forbruk' in df.columns:
                    consumption_column = 'KWH 60 Forbruk'
                elif 'KWH 15 Forbruk' in df.columns:
                    consumption_column = 'KWH 15 Forbruk'
                else:
                    st.error("Neither 'KWH 60 Forbruk' nor 'KWH 15 Forbruk' found in the CSV file.")
                    return

                grouped = df.groupby(['Date', 'Hour'])[consumption_column].apply(
                    lambda x: sum(map(float, x.str.replace(",", ".")))
                ).reset_index()

                grouped[consumption_column] = pd.to_numeric(grouped[consumption_column], errors='coerce')
                grouped.dropna(subset=[consumption_column], inplace=True)

                for date in grouped['Date'].unique():
                    date_data = grouped[grouped['Date'] == date]
                    hourly_consumption = date_data.groupby('Hour')[consumption_column].sum().to_dict()
                    monthly_hourly_consumption[date] = hourly_consumption

                all_consumption_data = []
                for date, hourly_data in monthly_hourly_consumption.items():
                    for hour, consumption_value in hourly_data.items():
                        all_consumption_data.append((date, hour, consumption_value))


                top_3_consumption = sorted(all_consumption_data, key=lambda x: x[2], reverse=True)[:3]




                date_with_highest_consumption = top_3_consumption[0][0]
                hourly_consumption_highest_date = \
                    grouped[grouped['Date'] == date_with_highest_consumption].groupby('Hour')[
                        consumption_column].sum().tolist()
                consumption = [round(value, 2) for value in hourly_consumption_highest_date]

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

    grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc = get_user_parameters(
        highest_hourly_consumption)


    st.write("")
    st.write("")
    st.write("**Top 3 Hours with Highest Consumption:**")
    for date, hour, consumption_value in top_3_consumption:
        st.write(f"Date: {date}, Hour: {hour}, Consumption: {consumption_value:.2f} kWh")
    st.write(f"Data for {date_with_highest_consumption} (highest consumption date) loaded successfully!")
    st.write("Hourly consumption:")
    st.write(consumption)

    # Consumption Profile Visualization
    hours = list(range(1, len(consumption) + 1))
    consumption_df = pd.DataFrame({'Hour': hours, 'Consumption (kWh)': consumption})

    # Create a line chart using Altair
    chart = alt.Chart(consumption_df).mark_line().encode(
        x=alt.X('Hour:O', title='Hour'),
        y=alt.Y('Consumption (kWh):Q', title='Consumption (kWh)'),
        tooltip=['Hour', 'Consumption (kWh)']
    ).properties(
        title='24-Hour Consumption Profile'
    ).configure_axis(
        labelAngle=0,
        grid=True,
        titlePadding=10
    ).configure_view(
        strokeWidth=0
    )

    st.altair_chart(chart, use_container_width=True)

    #grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc, max_soc = get_user_parameters(highest_hourly_consumption)

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

    daily_results = {}
    total_arbitrage_savings = 0
    daily_socs = {}
    current_soc = initial_soc
    for current_date in date_range:
        spot_prices = fetch_spot_prices(current_date, region)
        if not spot_prices:
            st.warning(f"Failed to fetch spot prices for {current_date}. Skipping.")
            continue

        results = optimize_bess(
            consumption, spot_prices, grid_threshold, battery_power, battery_capacity, battery_efficiency, min_soc,
            max_soc, current_soc
        )

        if results is None:
            st.warning(f"Optimization failed for {current_date}. Skipping.")
            continue

        charge_schedule, discharge_schedule, net_grid_load, arbitrage_savings = results

        daily_results[current_date] = (charge_schedule, discharge_schedule, arbitrage_savings)
        total_arbitrage_savings += arbitrage_savings

        final_soc = current_soc * battery_capacity
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

    date_options = [date.strftime('%Y-%m-%d') for date in date_range]
    selected_date_str = st.selectbox("Select a date to view the charge/discharge schedule", date_options)
    selected_date = datetime.datetime.strptime(selected_date_str, '%Y-%m-%d').date()  # Convert the string back to date

    if selected_date:
        if selected_date in daily_results:
            charge_schedule, discharge_schedule, daily_arbitrage_savings = daily_results[selected_date]

            # Charge/Discharge Schedule DataFrame
            schedule_data = {'Hour': range(24),
                             'Charge (kWh)': charge_schedule,
                             'Discharge (kWh)': discharge_schedule}
            schedule_df = pd.DataFrame(schedule_data)

            schedule_df['State'] = 'Idle'
            schedule_df.loc[schedule_df['Charge (kWh)'] > 0, 'State'] = 'Charging'
            schedule_df.loc[schedule_df['Discharge (kWh)'] > 0, 'State'] = 'Discharging'

            st.subheader(f"Charge/Discharge Schedule for {selected_date}")
            st.dataframe(schedule_df)

            # Create bar chart of charge/discharge schedule
            chart_data = pd.DataFrame({
                'Hour': range(24),
                'Charge': charge_schedule,
                'Discharge': discharge_schedule
            })

            # Transform the DataFrame to long format
            chart_data = chart_data.melt(id_vars='Hour', value_vars=['Charge', 'Discharge'],
                                         var_name='Type', value_name='kWh')

            # Ensure 'Type' is treated as a categorical variable
            chart_data['Type'] = chart_data['Type'].astype(str)

            # Altair chart with labels, tooltips, and cleaner boundaries
            chart = alt.Chart(chart_data).mark_bar().encode(
                x=alt.X('Hour:O', title='Hour'),
                y=alt.Y('kWh:Q', title='kWh'),
                color=alt.Color('Type:N', scale=alt.Scale(domain=['Charge', 'Discharge'], range=['green', 'red'])),
                tooltip=['Hour', 'Type', 'kWh']
            ).properties(
                title=f"Charge/Discharge Schedule for {selected_date}"
            ).configure_axis(
                labelAngle=0,
                grid=True,
                titlePadding=10
            ).configure_view(
                strokeWidth=0
            )

            st.altair_chart(chart, use_container_width=True)

            st.write(f"{selected_date.strftime('%d/%m/%Y')}'s Arbitrage Savings: {daily_arbitrage_savings:.2f} NOK")

            # Spot Prices Visualization
            spot_prices = fetch_spot_prices(selected_date, region)
            if spot_prices:
                spot_prices_df = pd.DataFrame({
                    'Hour': range(24),
                    'Spot Price (NOK/kWh)': spot_prices
                })

                spot_price_chart = alt.Chart(spot_prices_df).mark_line(color='blue').encode(
                    x=alt.X('Hour:O', title='Hour'),
                    y=alt.Y('Spot Price (NOK/kWh):Q', title='Spot Price (NOK/kWh)'),
                    tooltip=['Hour', 'Spot Price (NOK/kWh)']
                ).properties(
                    title=f'Spot Prices for {selected_date}'
                ).configure_axis(
                    labelAngle=0,
                    grid=True,
                    titlePadding=10
                ).configure_view(
                    strokeWidth=0
                )

                st.altair_chart(spot_price_chart, use_container_width=True)

        else:
            st.write("No data available for the selected date.")

        st.subheader("Combined Peak Shaving and Price Arbitrage Optimization")

        daily_combined_results = {}
        total_combined_savings = 0
        current_soc = initial_soc

        for current_date in date_range:
            spot_prices = fetch_spot_prices(current_date, region)
            if not spot_prices:
                continue

            results = optimize_combined_peak_arbitrage(
                consumption, spot_prices, grid_threshold, battery_power, battery_capacity,
                battery_efficiency, min_soc, max_soc, current_soc
            )

            if results is None:
                continue

            charge_schedule, discharge_schedule, net_grid_load, combined_savings, final_soc = results
            daily_combined_results[current_date] = (
            charge_schedule, discharge_schedule, net_grid_load, combined_savings)
            total_combined_savings += combined_savings
            current_soc = final_soc

        st.write(f"Total Savings from Combined Peak Shaving and Price Arbitrage: {total_combined_savings:.2f} NOK")

        # Visualization for selected date
        selected_date_combined_str = st.selectbox(
            "Select a date to view combined peak shaving and arbitrage schedule",
            date_options,
            key="combined_date_select"
        )
        selected_date_combined = datetime.datetime.strptime(selected_date_combined_str, '%Y-%m-%d').date()

        if selected_date_combined in daily_combined_results:
            charge_schedule, discharge_schedule, net_grid_load, daily_combined_savings = daily_combined_results[
                selected_date_combined]

            # Schedule DataFrame
            combined_df = pd.DataFrame({
                'Hour': range(24),
                'Consumption': consumption,
                'Charge (kWh)': charge_schedule,
                'Discharge (kWh)': discharge_schedule,
                'Net Grid Load': net_grid_load
            })

            st.subheader(f"Combined Schedule for {selected_date_combined}")
            st.dataframe(combined_df)

            # Combined visualization
            chart_data = pd.DataFrame({
                'Hour': range(24),
                'Consumption': consumption,
                'Net Grid Load': net_grid_load,
                'Charge': charge_schedule,
                'Discharge': discharge_schedule
            })

            base = alt.Chart(chart_data).encode(x=alt.X('Hour:O', title='Hour'))

            line1 = base.mark_line(color='blue').encode(
                y=alt.Y('Consumption:Q', title='kWh'),
                tooltip=['Hour', 'Consumption']
            )

            line2 = base.mark_line(color='green').encode(
                y=alt.Y('Net Grid Load:Q'),
                tooltip=['Hour', 'Net Grid Load']
            )

            bar = base.mark_bar(opacity=0.7).encode(
                y=alt.Y('Charge:Q', stack=None),
                y2=alt.Y2('Discharge:Q'),
                color=alt.condition(
                    alt.datum.Charge > 0,
                    alt.value('green'),
                    alt.value('red')
                ),
                tooltip=['Hour', 'Charge', 'Discharge']
            )

            threshold_line = alt.Chart(pd.DataFrame({'y': [grid_threshold]})).mark_rule(color='red').encode(y='y')

            combined_chart = alt.layer(line1, line2, bar, threshold_line).properties(
                title=f'Combined Peak Shaving and Arbitrage for {selected_date_combined}'
            ).configure_axis(
                labelAngle=0,
                grid=True,
                titlePadding=10
            ).configure_view(
                strokeWidth=0
            )

            st.altair_chart(combined_chart, use_container_width=True)

            st.write(f"Daily Combined Savings for {selected_date_combined}: {daily_combined_savings:.2f} NOK")

    with st.sidebar.expander("Savings Summary", expanded=True):
        st.sidebar.markdown("## 💰 Savings Summary")
        st.sidebar.markdown(f"**Total Savings from Peak Shaving for 6 months(winter)**: {total_savings:.2f} NOK")
        st.sidebar.markdown(f"**Total Savings from Peak Shaving for 6 months(summer)**: {total_savings * 44 / 104:.2f} NOK")
        st.sidebar.markdown(f"**Total Savings from Price Arbitrage for the month**: {total_arbitrage_savings:.2f} NOK")
        st.sidebar.markdown(f"**Total Savings from Combined Peak Shaving and Price Arbitrage**: {total_combined_savings:.2f} NOK")


if __name__ == "__main__":
    main()
