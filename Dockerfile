FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
CMD ["gunicorn", "--timeout", "120", "--workers", "1", "-b", "0.0.0.0:5000", "app:app"]
