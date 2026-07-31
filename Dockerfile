FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--timeout", "120", "--workers", "1", "app:app"]
