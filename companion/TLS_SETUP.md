# Verified HTTPS between Docker and Buddy Runner

Every non-loopback Runner address requires HTTPS with certificate-chain and
hostname verification. Docker's `host.docker.internal`, Tailscale names and
Tailscale IPs do not create HTTP exceptions. HTTP is supported only for literal
loopback addresses and `localhost`. Runner URLs are origins; credentials, paths,
queries and fragments are refused. HTTP and WebSocket redirects are refused
before forwarding the privileged Runner key.

The Runner still listens only on loopback, normally port **8765**. TLS does not
approve project roots or change provider permissions. A browser address remains
separate from the backend Runner address and requires its own reachable, trusted
transport. This setup does not install browser or Windows trust.

## Certificate material

Supply a PEM server certificate chain and its matching, unencrypted PEM private
key. Protect the key as a host-only secret readable by the Runner account; never
mount it into Docker, commit it, or send it to the browser. The leaf certificate
must cover the actual backend hostname, such as `host.docker.internal`, in its
Subject Alternative Name. Include loopback names/IPs if those HTTPS URLs will be
used. The issuer must be trusted by the backend, and the certificate must be
within its validity period. Invalid or missing TLS inputs stop startup before
the Runner creates or reads its persistent execution key.

For a normally trusted issuer, the backend needs no additional trust file. For
a private CA, use only its public certificate chain. An explicitly trusted
self-signed server certificate can also be used; renewing it requires replacing
that public trust file. Certificate/private-key export or new issuance remains
an operator action. The launcher does not generate credentials or alter trust.

## Runner startup

An operator can add TLS to the normal launcher arguments:

```powershell
local\start-buddy-runner.ps1 `
  -TlsCertificate 'C:\approved-host-only-location\runner-cert.pem' `
  -TlsKeyFile 'C:\approved-host-only-location\runner-key.pem'
```

The Python equivalents are `--tls-cert` and `--tls-key`. Both must be supplied.
Keep the existing approved state directory and intended launcher arguments;
this example adds no project roots, write permission or commands. The listener
remains `127.0.0.1:8765`. No TLS flag retains loopback HTTP for local clients.
The TLS server requires TLS 1.2 or newer.

## Docker trust scoped to Runner requests

`BUDDY_RUNNER_CA_FILE` adds a public PEM bundle to normal system certificate
trust for Buddy's Runner HTTP and WebSocket clients only. Hostname verification
and required certificate verification stay enabled. Invalid, oversized or
private-key-containing bundles fail closed; there is no HTTP fallback or
certificate-warning bypass. Existing system roots remain available.

The optional `docker/buddy/runner-tls.yaml` overlay changes only the existing
`buddy` backend service. It mounts a public file read-only at
`/etc/buddy/runner-ca.pem` and sets the Runner-specific trust variable. It adds
no listener, public port, private key, service or data-volume replacement.
`create_host_path: false` prevents a missing source from creating a directory.

Prepare an operator-selected public file and validate the intended Compose
configuration without printing resolved secret values:

```powershell
$env:BUDDY_RUNNER_CA_HOST_FILE = 'C:\approved-public-location\runner-ca.pem'
docker compose --project-name buddy --env-file docker/buddy/.env `
  -f docker/buddy/compose.yaml -f docker/buddy/runner-tls.yaml config --quiet
```

Use the existing deployment checkout, Compose project, environment file and
signing/encryption configuration. Applying the overlay and recreating the
backend are separate operator actions. Preserve the named `buddy-data` volume;
do not use `down -v`. Neither the frontend service nor unrelated application
changes are required for the trust mount itself. The running backend must also
contain the Runner-specific verified-client implementation.

After an approved startup/recreation, first verify a TLS handshake from the
backend without a bearer key. A successful handshake must verify both trust and
`host.docker.internal`; the unpaired provider health endpoint returns 401.
Then explicitly verify/save `https://host.docker.internal:8765` in Machines.
Changing a stored address or observed Runner instance resets affected provider
access and disables execution. Reconfigure and enable separately with fresh
machine/revision proof. Registration and certificate trust create no project
grant. Docker Desktop host routing must be checked on the actual deployment;
another Docker platform may require a separately reviewed private route.

## Expiry, renewal and rotation

Certificates expire. Startup rejects expired or not-yet-valid certificates and
logs a warning when the leaf expires within 30 days. These are startup checks,
not a recurring monitor or automatic renewal. The certificate is loaded once;
replacing the files does not reload the active listener.

Currently, applying a renewed server certificate requires a Runner restart.
That interrupts provider processes and invalidates paired project sessions.
The new instance must be observed and verified before enabling affected provider
access again. A future supported renewal and listener-reload mechanism should
avoid that interruption, but this change installs no scheduler or renewal
service and creates no persistent CA or issuance credentials.

With a stable private/public CA, leaf renewal can retain the backend's existing
CA trust. With a self-signed leaf, renewal changes the trusted certificate:
update the public mount and restart the backend to reload its client context.
For CA rotation, first supply an overlap bundle containing the old and new
public authorities and reload backend trust; then replace the server chain;
finally remove the old authority after the migration. Each issuance, trust-file
change and live restart requires the appropriate operator approval. Prefer an
already managed issuer and renewal mechanism when one is available; an
operator-supplied PEM file alone is not automatic renewal.

The implementation uses Python's [verifying TLS client context](https://docs.python.org/3.11/library/ssl.html#ssl.PROTOCOL_TLS_CLIENT)
and aiohttp's [connector SSL context](https://docs.aiohttp.org/en/stable/client_advanced.html#ssl-control-for-tcp-sockets).
