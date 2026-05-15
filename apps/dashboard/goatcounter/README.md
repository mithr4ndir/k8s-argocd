# GoatCounter

Self-hosted, privacy-friendly analytics for static sites. Currently fronts
[docs.herro.me](https://docs.herro.me); designed to host additional sites
behind the same instance later.

## Topology

```
visitor browser ──beacon──> NPM (192.168.1.150)
                            └─https─> gc.herro.me
                                       └─http─> 192.168.1.227:8080 (Service: goatcounter)
                                                 └─ Pod: goatcounter (namespace: dashboard)
                                                     └─ PVC: goatcounter-data (k8s-nfs, 1Gi)
                                                         └─ SQLite at
                                                            /home/user/.local/share/goatcounter/db/goatcounter.sqlite3
```

NPM terminates TLS. The pod runs plain HTTP inside the cluster.

## Operational hazards

- **Never `kubectl rollout restart`** this deployment. SQLite + NFS does not
  tolerate overlapping writers. To restart safely:

  ```bash
  kubectl scale deploy goatcounter -n dashboard --replicas=0 \
    && sleep 5 \
    && kubectl scale deploy goatcounter -n dashboard --replicas=1
  ```

- Strategy is `Recreate` for the same reason. Same hazard pattern as the
  *arr apps.
- Data persistence relies on TrueNAS ZFS snapshots of the
  `tank/k8s/dashboard-goatcounter-data-*` dataset. Verify snapshot
  retention if you start treating the analytics as load-bearing.

## First-run bootstrap

After the manifests sync via ArgoCD and the pod is `Running`, do this once:

1. **Pick an admin password and write it to 1Password.**

   ```bash
   # Generate
   PW=$(openssl rand -base64 24)

   # Store in 1Password Infrastructure vault as item:
   #   Title: GoatCounter Admin
   #   Username: naerois@gmail.com
   #   Password: $PW
   #   URL: https://gc.herro.me
   op item create --vault Infrastructure --category login \
       --title "GoatCounter Admin" \
       --url "https://gc.herro.me" \
       --generate-password='letters,digits,symbols,32'
   ```

2. **Create the docs.herro.me site row inside GoatCounter.**

   ```bash
   POD=$(kubectl -n dashboard get pod -l app.kubernetes.io/name=goatcounter -o name | head -n1)
   kubectl -n dashboard exec -it "$POD" -- goatcounter db create site \
       -vhost docs.herro.me \
       -user.email naerois@gmail.com \
       -user.password "$PW" \
       -db sqlite+/home/user/.local/share/goatcounter/db/goatcounter.sqlite3
   ```

3. **Configure NPM proxy host** at `https://192.168.1.150:81/`:

   - Domain Names: `gc.herro.me`
   - Scheme: `http`
   - Forward Hostname / IP: `192.168.1.227`
   - Forward Port: `8080`
   - Block Common Exploits: on
   - Websockets Support: on (used by the live-update dashboard)
   - SSL: Request a new Let's Encrypt certificate, Force SSL, HTTP/2,
     HSTS enabled.

   Make sure the DNS record for `gc.herro.me` resolves to the
   WAN address that NPM is published behind (same as
   `docs.herro.me` if you proxy that through NPM, or your usual
   ingress IP).

4. **Generate an API key** at `https://gc.herro.me/settings/api`,
   scopes: `Read statistics`. Store as 1Password item
   `GoatCounter API Key` in the Infrastructure vault. This key is
   what the Homepage `customapi` widget will use (separate PR).

5. **Wire the beacon into the MkDocs site** (separate commit in the
   `quasarlab-docs` repo). Beacon snippet:

   ```html
   <script data-goatcounter="https://gc.herro.me/count"
           async src="//gc.herro.me/count.js"></script>
   ```

## Upgrades

```bash
# Bump tag in values.yaml, then:
make all
git add -A
git commit -m "feat(goatcounter): bump to vX.Y.Z"
```

Verify the upstream release notes for breaking schema changes before
applying. GoatCounter runs auto-migrations on start (`-automigrate` flag),
so most bumps are safe, but always check.
