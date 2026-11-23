DASHBOARD (Task 4)

How to run
  pip install -r requirements.txt
  streamlit run app.py
Run from the dashboard folder with forecasts.csv present in the same folder.

What it shows
  - Interactive map of the 5 Cairo districts colored by predicted AQI category
  - Switch between 24h (horizon 1), 48h (horizon 2), and 72h (horizon 3)
  - District selector with predicted PM2.5, risk probability, and alert level
  - Plain-language health advice box and alert reliability note