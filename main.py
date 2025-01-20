import requests
import time
import os
from datetime import datetime
import logging
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import config  # Add this import

# Load environment variables from .env file
load_dotenv()

# Set up logging
log_level = logging.DEBUG if config.LOGGING['verbose'] else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('dns_updater.log'),
        logging.StreamHandler()
    ]
)

class CloudflareDNSUpdater:
    def __init__(self):
        # Cloudflare API configuration
        self.cf_api_token = os.getenv('CF_API_TOKEN')
        self.cf_api_key = os.getenv('CF_API_KEY')
        self.cf_email = os.getenv('CF_EMAIL')
        self.zone_id = os.getenv('CF_ZONE_ID')
        self.record_name = os.getenv('CF_RECORD_NAME')
        # Use config value but allow override from environment
        self.check_interval = int(os.getenv('CHECK_INTERVAL', str(config.TIMERS['check_interval'])))
        
        # Determine authentication method
        if self.cf_api_token:
            logging.debug("Using API Token authentication")
            self.headers = {
                "Authorization": f"Bearer {self.cf_api_token}",
                "Content-Type": "application/json"
            }
        elif self.cf_api_key and self.cf_email:
            logging.debug("Using API Key authentication")
            self.headers = {
                "X-Auth-Email": self.cf_email,
                "X-Auth-Key": self.cf_api_key,
                "Content-Type": "application/json"
            }
        else:
            raise ValueError("Missing authentication credentials. Provide either CF_API_TOKEN or both CF_API_KEY and CF_EMAIL")
        
        if not self.zone_id or not self.record_name:
            raise ValueError("Missing required environment variables: CF_ZONE_ID and CF_RECORD_NAME are required")
        
        self.cf_api_url = f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/dns_records"
        
        # Keep track of the current IP
        self.current_ip = None
        self.record_id = None
        
        # Set up requests session with retry logic and timeouts
        self.session = requests.Session()
        
        # Create retry strategy based on urllib3 version
        try:
            retry_strategy = Retry(
                total=3,  # number of retries
                backoff_factor=1,  # wait 1, 2, 4 seconds between retries
                status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry on
                allowed_methods=["GET", "PUT"]  # New parameter name
            )
        except TypeError:
            # Fallback for older versions of urllib3
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                method_whitelist={"GET", "PUT"}  # Old parameter name
            )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=3,
            pool_maxsize=3
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set default timeouts for all requests
        self.session.timeout = (
            config.TIMERS['timeout']['connect'],
            config.TIMERS['timeout']['read']
        )
        
        logging.debug("Initializing CloudflareDNSUpdater")
        logging.debug(f"Using API URL: {self.cf_api_url}")
        logging.debug(f"Check interval set to: {self.check_interval} seconds")
        
        # Test connection to Cloudflare API if not disabled
        if not config.DNS_SETTINGS['skip_connection_test']:
            try:
                logging.debug("Testing connection to Cloudflare API...")
                test_response = self.session.get(
                    "https://api.cloudflare.com/client/v4/user/tokens/verify",
                    headers=self.headers,
                    timeout=(5, 10)
                )
                test_response.raise_for_status()
                logging.debug("Successfully connected to Cloudflare API")
            except requests.exceptions.ConnectTimeout:
                logging.error("Connection timeout while connecting to Cloudflare API - check your network connection")
                raise
            except requests.exceptions.ReadTimeout:
                logging.error("Read timeout while connecting to Cloudflare API")
                raise
            except requests.exceptions.SSLError:
                logging.error("SSL Error - there might be a proxy or SSL inspection interfering with the connection")
                raise
            except requests.exceptions.ConnectionError as e:
                logging.error(f"Connection Error: {e}")
                logging.error("Check if api.cloudflare.com is accessible from your network")
                raise
            except Exception as e:
                logging.error(f"Unexpected error while testing Cloudflare API connection: {e}")
                raise
        else:
            logging.debug("Skipping Cloudflare connection test")

    def get_external_ip(self):
        """Get the current external IP address."""
        logging.debug("Attempting to get external IP address")
        try:
            for service in config.IP_CHECK_SERVICES:
                try:
                    logging.debug(f"Trying IP service: {service}")
                    response = self.session.get(service, timeout=10)
                    response.raise_for_status()
                    
                    # Check if this is a JSON service
                    if any(json_service in service for json_service in config.JSON_SERVICES):
                        ip = response.json()['ip']
                    else:
                        ip = response.text.strip()
                        
                    logging.debug(f"Successfully got IP from {service}: {ip}")
                    return ip
                except Exception as e:
                    logging.warning(f"Failed to get IP from {service}: {e}")
                    continue
            
            logging.error("All IP detection services failed")
            return None
            
        except Exception as e:
            logging.error(f"Failed to get external IP: {e}")
            return None

    def get_dns_record(self):
        """Get the current DNS record ID and content."""
        logging.debug(f"Getting DNS record for {self.record_name}")
        try:
            logging.debug("Initiating request to Cloudflare API...")
            response = self.session.get(
                self.cf_api_url,
                headers=self.headers,
                params={'name': self.record_name},
                timeout=(5, 10)  # (connect timeout, read timeout)
            )
            logging.debug("Request completed")
            response.raise_for_status()
            
            result = response.json()
            logging.debug(f"Cloudflare API response: {result}")
            
            if not result.get('success', False):
                logging.error(f"Cloudflare API error: {result.get('errors', [])}")
                return None
                
            records = result.get('result', [])
            if records:
                self.record_id = records[0]['id']
                logging.debug(f"Found record ID: {self.record_id}")
                return records[0]['content']
            logging.debug("No DNS records found")
            return None
        except Exception as e:
            logging.error(f"Failed to get DNS record: {e}")
            return None

    def update_dns_record(self, new_ip):
        """Update the DNS record with the new IP."""
        logging.debug(f"Attempting to update DNS record to {new_ip}")
        if not self.record_id:
            logging.error("No record ID found")
            return False

        try:
            data = {
                'content': new_ip,
                'name': self.record_name,
                'proxied': config.DNS_SETTINGS['proxied'],
                'type': 'A',
                'ttl': config.DNS_SETTINGS['ttl']
            }
            logging.debug(f"Update request data: {data}")

            response = self.session.put(
                f"{self.cf_api_url}/{self.record_id}",
                headers=self.headers,
                json=data,
                timeout=10
            )
            response.raise_for_status()
            
            result = response.json()
            logging.debug(f"Cloudflare API response: {result}")
            
            if not result.get('success', False):
                logging.error(f"Cloudflare API error: {result.get('errors', [])}")
                return False
                
            logging.info(f"Successfully updated DNS record to {new_ip}")
            return True
        except Exception as e:
            logging.error(f"Failed to update DNS record: {e}")
            return None

    def run(self):
        """Main loop to check and update IP address."""
        logging.debug("Starting main update loop")
        while True:
            try:
                # Get current external IP
                logging.debug("Starting new check cycle")
                new_ip = self.get_external_ip()
                if not new_ip:
                    logging.warning("Could not get external IP, waiting before retry")
                    time.sleep(config.TIMERS['retry_interval'])
                    continue

                # Get current DNS record
                dns_ip = self.get_dns_record()
                if not dns_ip:
                    logging.warning("Could not get DNS record, waiting before retry")
                    time.sleep(config.TIMERS['retry_interval'])
                    continue

                # First run initialization
                if self.current_ip is None:
                    self.current_ip = new_ip
                
                # Check if either our cached IP or DNS record doesn't match the current IP
                if new_ip != self.current_ip or new_ip != dns_ip:
                    logging.info(f"Update needed - External IP: {new_ip}, Cached IP: {self.current_ip}, DNS IP: {dns_ip}")
                    if self.update_dns_record(new_ip):
                        self.current_ip = new_ip
                else:
                    logging.info(f"No IP change detected: {new_ip} (DNS record matches)")

            except Exception as e:
                logging.error(f"Unexpected error in main loop: {e}")
                logging.debug(f"Error details: {str(e)}", exc_info=True)
                time.sleep(config.TIMERS['retry_interval'])  # Use configured retry interval
                continue

            # Wait for the configured interval before next check
            logging.info(f"Waiting {self.check_interval} seconds before next check")
            time.sleep(self.check_interval)

if __name__ == "__main__":
    logging.info("Starting Cloudflare DNS Updater")
    logging.debug("Debug logging enabled")
    
    # Add network diagnostic information
    try:
        import socket
        logging.debug("Performing network diagnostics...")
        
        # Test DNS resolution
        try:
            cloudflare_ip = socket.gethostbyname('api.cloudflare.com')
            logging.debug(f"Successfully resolved api.cloudflare.com to {cloudflare_ip}")
        except socket.gaierror as e:
            logging.error(f"Failed to resolve api.cloudflare.com: {e}")
        
        # Test basic connectivity
        try:
            test_socket = socket.create_connection(('api.cloudflare.com', 443), timeout=5)
            test_socket.close()
            logging.debug("Successfully established test connection to api.cloudflare.com")
        except Exception as e:
            logging.error(f"Failed to connect to api.cloudflare.com: {e}")
            logging.error("This might indicate a firewall or proxy issue")
    
    except Exception as e:
        logging.error(f"Error during network diagnostics: {e}")
    
    updater = CloudflareDNSUpdater()
    updater.run()
