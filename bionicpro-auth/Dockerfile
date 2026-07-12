FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости отдельным слоем для кеширования.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код сервиса.
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
