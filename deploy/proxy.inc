proxy_pass http://app:8000;
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $http_host;
# Адрес берётся из соединения, а не дописывается к присланному: подделать его из запроса нельзя.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Request-ID $request_id;
proxy_read_timeout 60s;
