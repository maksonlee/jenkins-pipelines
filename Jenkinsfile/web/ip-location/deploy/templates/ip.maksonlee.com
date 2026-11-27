server {
    listen 8443 ssl http2 proxy_protocol;
    server_name ip.maksonlee.com;

    ssl_certificate /etc/letsencrypt/live/ip.maksonlee.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ip.maksonlee.com/privkey.pem;

    access_log off;
    error_log /dev/null emerg;

    location = /health { return 404; }
    location ^~ /health/ { return 404; }

    location / {
        proxy_pass http://127.0.0.1:38170;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Forwarded "";
        proxy_set_header X-Real-IP "";
        proxy_set_header CF-Connecting-IP "";
        proxy_intercept_errors off;
        proxy_connect_timeout 2s;
        proxy_send_timeout 5s;
        proxy_read_timeout 12s;
    }
}
