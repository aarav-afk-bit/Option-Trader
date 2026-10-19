FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPTION_TRADER_DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY option_trader ./option_trader
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "option_trader", "--host", "0.0.0.0", "--port", "8000"]
