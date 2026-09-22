FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV HOST=0.0.0.0 PORT=8787 ALLOW_LAN=1 PYTHONUNBUFFERED=1
EXPOSE 8787
VOLUME ["/app/data"]
# Optional OpenSCAD is intentionally not installed in the minimal runtime.
CMD ["python", "run.py"]
