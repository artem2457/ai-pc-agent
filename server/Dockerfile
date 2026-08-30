FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server /app/server
COPY web /app/web
COPY agent /app/agent
COPY winpe /app/winpe
COPY usb_maker /app/usb_maker
COPY linux_usb /app/linux_usb
COPY shared /app/shared
RUN mkdir -p /app/data
WORKDIR /app/server
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
