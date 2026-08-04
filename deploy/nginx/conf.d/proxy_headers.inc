# Shared proxy headers.
#
# X-Forwarded-For is *set* to $remote_addr rather than appended with
# $proxy_add_x_forwarded_for. Appending preserves whatever the client sent, and since the
# gateway is configured to trust exactly one hop, a client that sends its own multi-entry
# chain would shift the entry the gateway reads. Setting it means the gateway always sees a
# single, trustworthy value. (See tests/unit/test_providers_and_platform.py::TestClientIpResolution.)
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header X-Correlation-Id  $request_id;
proxy_connect_timeout 10s;
