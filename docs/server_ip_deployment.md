# Deploy the faucet on one server using an IP address

This setup runs PostgreSQL, the FastAPI API, the distributor worker, Next.js, and Nginx in one Docker Compose project. Open `http://SERVER_IP:3000` in the browser until you have a domain. Nginx is the public entrypoint; PostgreSQL and Next.js have no host ports, and FastAPI remains bound to server loopback by the base Compose file. The Next.js route handlers proxy faucet calls to FastAPI over the Docker network.

The production overlay is [`backend/compose.server.yaml`](../backend/compose.server.yaml). Use it alongside [`backend/compose.yaml`](../backend/compose.yaml); local development can continue to use the base file alone. Do not start a second copy of the API or worker against the same database and distributor key.

## 1. Put the code and required local files on the server

Install Docker Engine and the Compose plugin on the server. Commit and push the deployment changes from the current `frontend` branch, then clone that branch on the server (use your authenticated Git URL if the repository is private):

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /srv/roburna-faucet
git clone --branch frontend https://github.com/roburna-labs/Roburna-Blockchain-Faucet.git /srv/roburna-faucet
```

The following files are ignored by Git and must be transferred securely from your working machine into the same paths on the server:

- `contracts/deployments/159.json` — the **active new** deployment, address `0xb913025fd2067F996CB00B70a5DD0aB81e8FbaD4`. Do not transfer the old `159-old.json` as the active file.
- `backend/config/chains.json` — the enabled Roburna chain configuration.
- `backend/secrets/distributor.json` — the encrypted keystore for distributor `0x546501e0c1d35822CcA63ef2A4787Ff1c2367079`.

For example, with the repository checked out at `/srv/roburna-faucet` on the server, run these from the local repository root, replacing `USER` and `SERVER_IP`:

```bash
ssh USER@SERVER_IP 'mkdir -p /srv/roburna-faucet/contracts/deployments /srv/roburna-faucet/backend/config /srv/roburna-faucet/backend/secrets'
scp contracts/deployments/159.json USER@SERVER_IP:/srv/roburna-faucet/contracts/deployments/159.json
scp backend/config/chains.json USER@SERVER_IP:/srv/roburna-faucet/backend/config/chains.json
scp backend/secrets/distributor.json USER@SERVER_IP:/srv/roburna-faucet/backend/secrets/distributor.json
```

On the server, make the keystore readable only by the account that runs the worker (`BACKEND_UID`/`BACKEND_GID` below):

```bash
cd /srv/roburna-faucet/backend
chmod 600 secrets/distributor.json
id -u
id -g
```

The deployer and admin private keys are **not** needed on this server. The distributor keystore is encrypted; its password goes in a separate server file.

## 2. Configure the server origin and secrets

Copy [`backend/compose.env.example`](../backend/compose.env.example) to ignored `backend/compose.env`. Set a strong database password, retain the existing rate-limit secret if migrating data, and make these settings match the URL users will open:

```dotenv
BACKEND_SIWE_DOMAIN=SERVER_IP:3000
BACKEND_SIWE_URI=http://SERVER_IP:3000
BACKEND_RPC_URLS={"ROBURNA_TESTNET":"https://preseed-testnet-1.roburna.com"}
BACKEND_TRUSTED_PROXY_CIDRS=["172.30.159.3/32"]
BACKEND_PORT=8001
FAUCET_PUBLIC_BIND_IP=0.0.0.0
FAUCET_PUBLIC_PORT=3000
FAUCET_NETWORK_SUBNET=172.30.159.0/24
FAUCET_FRONTEND_IP=172.30.159.3
NEXT_PUBLIC_ROBURNA_RPC_URL=https://preseed-testnet-1.roburna.com
```

Replace `SERVER_IP` with the actual public IPv4 address, without `http://`. If you change the public port, change **both** SIWE settings to that port. Use the same URL in the browser; `localhost:3000` will produce a different SIWE domain. `POSTGRES_PASSWORD` and the password in `BACKEND_DATABASE_URL` must match; the URL host stays `db`. Set `BACKEND_RATE_LIMIT_HASH_SECRET` to the same value as local `compose.env` when migrating the database, so historical IP/network rate-limit records still match. For a fresh database only, generate a new value with `python3 -c 'import secrets; print(secrets.token_hex(32))'`. Keep it stable across restarts. Set `BACKEND_UID` and `BACKEND_GID` from `id -u` and `id -g`. Restrict `compose.env` with `chmod 600 compose.env`.

The overlay gives the Next.js container the fixed address `172.30.159.3`. Nginx overwrites `X-Faucet-Client-IP` with the TCP peer address; Next.js forwards that as `X-Forwarded-For`; FastAPI trusts forwarding only from `172.30.159.3/32`. This preserves per-IP and network-prefix limits without trusting an IP header supplied by a browser. If the chosen Docker subnet conflicts with an existing server network, choose another private `/24` and update **all three** values: `FAUCET_NETWORK_SUBNET`, `FAUCET_FRONTEND_IP`, and `BACKEND_TRUSTED_PROXY_CIDRS`.

Create the worker-only password file outside the repository. The value must be the password for the transferred encrypted distributor keystore:

```bash
sudo install -d -o root -g "$(id -gn)" -m 0750 /etc/roburna-faucet
sudo install -o root -g "$(id -gn)" -m 0640 /dev/null /etc/roburna-faucet/worker.env
sudoedit /etc/roburna-faucet/worker.env
```

Put `BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD=<keystore password>` on its own line. Set `BACKEND_WORKER_ENV_FILE=/etc/roburna-faucet/worker.env` in `compose.env`. Keep the password out of commands and the repo.

## 3. Preserve local claim history (recommended)

The local PostgreSQL database contains cooldown history. Starting a fresh server database resets those cooldowns. When ready for the final cutover, stop the **local** API and worker to prevent new claims, then create a database dump on the local machine:

```bash
cd backend
docker compose --env-file compose.env stop api worker
umask 077
docker compose --env-file compose.env exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /tmp/roburna-faucet.dump
scp /tmp/roburna-faucet.dump USER@SERVER_IP:/tmp/roburna-faucet.dump
```

On the server, start only PostgreSQL, restore the dump, and then remove the temporary copy:

```bash
cd /srv/roburna-faucet/backend
chmod 600 /tmp/roburna-faucet.dump
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env up -d db
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env exec -T db sh -lc 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' < /tmp/roburna-faucet.dump
rm /tmp/roburna-faucet.dump
```

If you intentionally start with an empty database, skip this section and understand that previous wallet cooldowns will not be present. Never run both the local and server workers against the same distributor key at once.

## 4. Start and check the stack

Allow the chosen public TCP port (default `3000`) in the cloud firewall/security group. Keep database port `5432` and FastAPI port `8001` closed to the internet. Then run on the server:

```bash
cd /srv/roburna-faucet/backend
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env config --quiet
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env up -d --build
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env ps
docker compose -f compose.yaml -f compose.server.yaml --env-file compose.env logs --tail=80 api worker frontend gateway
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:3000/api/faucet/chains
```

The final response should show faucet `0xb913025fd2067F996CB00B70a5DD0aB81e8FbaD4`. From your own browser, open `http://SERVER_IP:3000` and test with a wallet holding **zero RBAT**: the initial recipient cap and fixed payout are both 10 RBAT. Confirm the claim reaches `confirmed` and the wallet receives 10 RBAT. A health response alone does not test signing or transaction submission.

The temporary IP URL uses plain HTTP. Move to a domain with HTTPS when available; at that point update `BACKEND_SIWE_DOMAIN` and `BACKEND_SIWE_URI` to the exact new browser origin and restart the API. `NEXT_PUBLIC_ROBURNA_RPC_URL` is compiled into the browser bundle, so changing it requires rebuilding the frontend image. The server-side `FAUCET_API_URL` stays `http://api:8000` inside Compose.
