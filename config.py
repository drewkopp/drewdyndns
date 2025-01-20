# IP Services Configuration
IP_CHECK_SERVICES = [
    'https://api.ipify.org?format=json',
    'https://ifconfig.me/ip',
    'https://api.ipify.org',
    'https://icanhazip.com'
]

# Service response types
JSON_SERVICES = ['api.ipify.org?format=json']

# DNS Record Configuration
DNS_SETTINGS = {
    'proxied': False,  # Whether to proxy through Cloudflare
    'ttl': 1,  # TTL value for DNS record
    'skip_connection_test': False  # Whether to skip initial Cloudflare connection test
}

# Logging Configuration
LOGGING = {
    'verbose': True,  # Enable debug logging
}

# Timing Configuration (in seconds)
TIMERS = {
    'check_interval': 3600,  # Default interval between checks
    'retry_interval': 60,    # How long to wait after an error before retry
    'timeout': {
        'connect': 5,        # Connection timeout
        'read': 10          # Read timeout
    }
} 