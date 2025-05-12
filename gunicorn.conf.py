import multiprocessing

# Gunicorn configuration for async workers
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'aiohttp.GunicornWebWorker'
bind = '0.0.0.0:8080'
timeout = 120
keepalive = 5
errorlog = '-'
accesslog = '-'
capture_output = True