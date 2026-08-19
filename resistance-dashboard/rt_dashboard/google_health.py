"""Google Health API client for body weight and sleep (legacy Fit fallback).

Uses OAuth2 refresh-token flow:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN

Preferred scopes (Google Health API):
  https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
  https://www.googleapis.com/auth/googlehealth.sleep.readonly
"""
