DEPLOYMENT, END-TO-END INTEGRATION & ACCURACY CHECK (Task 5 - Yousif Sherif)

What it does
1. run_chain.py: Runs the full end-to-end chain (syncs alert_config.json, executes Task 1 pipeline, atomically distributes forecasts.csv to Task 3 and Task 4, verifies Task 3 FastAPI endpoints, tests alert dispatch, and validates Task 4 dashboard).
2. check_accuracy.py: Compares past forecasts in forecast_log.csv against actual observed PM2.5 from Open-Meteo and reports MAE, RMSE, bias, and alert hit/miss outcomes.
3. deploy/ & DEPLOYMENT_GUIDE.md: Production configurations (Render, Streamlit Cloud, Dockerfile, GitHub Actions daily schedule) and step-by-step deployment/redeployment instructions.

One command to run (from the project root containing task1_pipeline, task3_api_bot, task4_dashboard, task5_deploy)
  python task5_deploy/run_chain.py

Offline / cached data smoke test (no internet required)
  python task5_deploy/run_chain.py --skip-pipeline

Run recent forecast accuracy check
  python task5_deploy/check_accuracy.py

Outputs
  task5_deploy/sample_output/chain_verification_report.json   End-to-end integration test results
  task5_deploy/sample_output/accuracy_comparison.csv          Row-by-row predicted vs. observed PM2.5
  task5_deploy/sample_output/accuracy_summary.json            MAE, RMSE, and bias summary by horizon & district
