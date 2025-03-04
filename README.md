BESS Size Calculator

This Streamlit application calculates the optimal size and operation of a Battery Energy Storage System (BESS) for peak shaving and price arbitrage. It allows users to input their hourly energy consumption, either manually or via CSV upload, and configure various BESS parameters to simulate and optimize its performance.

Features

-   Consumption Data Input:
    -      Manual entry of 24-hour consumption profile.
    -      CSV upload with automatic parsing and aggregation of hourly consumption data over a specified date range. Data is to be downloaded from Elhub
-   BESS Parameter Configuration:
    -      Battery power selection.
    -      Grid import threshold setting.
    -      C-Rate selection and automatic battery capacity calculation.
    -      Battery efficiency, minimum, and maximum state of charge (SOC) configuration.
-  Spot Price Integration:
    -      Fetches hourly spot prices from `hvakosterstrommen.no` API.
    -      Handles potential API errors and missing data gracefully.
-   Optimization:
    -   Peak Shaving Analysis: Calculates potential savings by reducing peak consumption below a user-defined threshold.
    -   Price Arbitrage Optimization: Determines optimal charging and discharging schedules based on spot prices to maximize savings.
    -   Combined Peak Shaving and Price Arbitrage Optimization: Optimizes the BESS operation to combine both peak shaving and price arbitrage strategies.
-   Visualization and Reporting:
    -   Interactive charts for consumption profiles, charge/discharge schedules, and spot prices.
    -   Detailed reports of savings from peak shaving and price arbitrage.
    -   Displays combined peak shaving and arbitrage charts.
    -   Displays the total savings of the combined method.
-   Battery SOC API Integration:
    -   Allows users to enter a site ID to fetch the current battery SOC from an external API.
    -   Uses the fetched SOC as the initial SOC for optimization, or defaults to the maximum SOC if fetching fails.

Prerequisites

-      Python 3.6+
-      Streamlit
-      Pandas
-      Altair
-   Requests

Usage

1.  Run the Streamlit application on Terminal/cmd:

   streamlit run bess_calculator.py
    

2.  The application will open in your web browser.

3.  Follow the instructions on the sidebar to input your consumption data and configure the BESS parameters.

4.  View the generated charts and reports on the main page.

Input Data

-   Manual Entry: Enter your 24-hour consumption profile directly into the application.
-   CSV Upload: Upload a CSV file with hourly consumption data. The CSV file is downloaded from Elhub and has a "Fra" datetime column, and either "KWH 60 Forbruk" or "KWH 15 Forbruk" column containing the consumption data. The date format in the CSV needs to be day first.

API Integration

-   The application uses the `hvakosterstrommen.no` API to fetch spot prices.
-   We can integrate with an external API to fetch the battery's current state of charge (SOC) by providing the site ID, API URL, username, and password.

Optimization Logic

-   Peak Shaving: The application calculates the amount of energy that needs to be discharged from the battery to keep the grid load below the specified threshold.
-   Price Arbitrage: The application identifies the hours with the lowest and highest spot prices and schedules charging and discharging accordingly to maximize savings.
-   Combined Optimization- Peak shaving Priority followed by price Arbitrage: The application prioritizes peak shaving by ensuring enough battery capacity is available during peak hours. Any remaining capacity is then used for price arbitrage.

License

This project is licensed under the MIT License.
