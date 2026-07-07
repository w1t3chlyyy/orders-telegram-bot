<<<<<<< HEAD
FROM python:3.10-slim

WORKDIR /app

# Устанавливаем зависимости напрямую (без requirements.txt)
RUN pip install --no-cache-dir aiogram==3.0.0 python-dotenv==1.0.0

COPY bot.py .

CMD ["python", "bot.py"]
=======
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
>>>>>>> 3bcd9705ab79c2d098cb6f0bf7f25acd3a603f67
