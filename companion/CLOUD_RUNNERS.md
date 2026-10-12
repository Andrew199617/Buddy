# Cloud Runners (design, not built)

A cloud Runner is a Buddy Runner in a container that Buddy starts for one
person. It lets someone use Claude and ChatGPT without connecting a computer,
or while their computer is off. This is a design for later; nothing here is
built yet.

**Cloud Runners stay off unless an administrator grants them, per group, until
Buddy charges for cloud use.** Containers cost real CPU, memory and disk, so
they are not offered to everyone the way connecting your own computer is.

## How it fits what exists

A cloud Runner is just another connected computer. The container runs the same
`buddy-runner run` as the Buddy app and dials out to Buddy over the same
WebSocket (see [connect mode](README.md#connect-mode-the-runner-dials-out-to-buddy)).
Buddy already treats such a computer as the user's own: their models, their
sign-in choice, their Projects relay, owner-only access. The new parts are who
creates the container and when it runs.

| Piece                 | Today (your computer)                      | Cloud Runner                                    |
| --------------------- | ------------------------------------------ | ----------------------------------------------- |
| Machine record        | `subscriptions.machines`, `kind: 'runner'` | Same list, `kind: 'cloud'`                      |
| Who starts the Runner | The Buddy app                              | The cloud manager                               |
| Credential            | Enrolled by the app, kept with DPAPI       | Enrolled by the container, kept in its volume   |
| Shared folders        | Chosen in the app                          | The container's own workspace volume only       |
| Commands              | Allowed in the app                         | Allowed by policy; the container is the sandbox |

## Parts

### Runner image

- Python with `buddy_runner`, Claude Code and Codex, pinned and rebuilt on a
  schedule.
- Runs as a non-root user with a read-only root filesystem, one writable
  workspace volume per person, and no Docker socket or host mounts.
- Starts `buddy-runner run --root /workspace --write --allow-host-execution`.
  The container is the boundary, so commands may run inside it.

### Cloud manager

A small service that owns the container platform's credentials. Buddy itself
never gets the Docker socket or cloud keys; it calls the manager with its own
token.

| Call                                          | Does                                                                                          |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `POST /runners` `{user_id, enrollment_token}` | Creates the volume and container; hands the single-use enrollment token to the container once |
| `POST /runners/{id}/start`, `/stop`           | Starts or stops the container; the volume stays                                               |
| `DELETE /runners/{id}`                        | Removes the container and its volume                                                          |
| `GET /runners/{id}`                           | State, image version, last start and stop                                                     |
| `GET /usage?since=`                           | Metering records (see below)                                                                  |

Enrolling reuses `create_enrollment` and `redeem_enrollment`: Buddy asks for a
60-second token for the person, passes it to the manager, and the container
redeems it on first start. Buddy stores only the credential's hash, as for any
computer.

### Isolation

Full access runs arbitrary commands, so each container needs a stronger
boundary than a shared kernel:

- gVisor (`runsc`), or Kata/Firecracker micro-VMs where available.
- CPU, memory, process-count and disk quotas per container.
- Network: outbound only, through a proxy that blocks private ranges and cloud
  metadata addresses. Buddy is reached by its public address, like any other
  computer.

### Sign-in

- **Claude token** and **API key** modes work as they are: Buddy hands the
  secret to each process and the container never stores it.
- **The computer's own sign-in** (device code inside the container) is
  possible, but it keeps a plan's sign-in on a server Buddy runs. Offer it
  only where the provider's terms allow it, and only for the person's own plan.

### Starting and stopping

- **Idle stop.** The manager stops a container after a configurable time with
  no running processes and no relayed Projects session.
- **Start on demand.** When a chat turn or the Projects page needs a stopped
  cloud Runner, Buddy asks the manager to start it, shows "Starting your cloud
  computer…", and waits for the Runner to connect (up to about a minute)
  before running the turn.
- **Removal.** Deleting the person (`forget_user`) or the administrator's
  **Remove** deletes the container and volume through the manager.

## Policy

Additions to `subscriptions.policy`:

- `cloud`: `off` (default) or `groups`.
- `cloud_group_ids`: the groups granted a cloud Runner.
- `cloud_limits`: CPU, memory, disk, idle minutes, and monthly hours per person.

Removing someone's grant stops their container and disconnects it with the
existing "not allowed" close code; the volume is kept for a grace period.

## Metering, for billing later

The manager records, per person and container:

- start and stop events with timestamps;
- container seconds, CPU seconds and peak memory;
- disk size per day and outbound bytes.

Buddy stores these in a `cloud_usage` table and shows them in Settings →
Machines and the administrators' overview. Hooks keep billing separate:
`on_runner_start`, `on_runner_stop` and a periodic `record_usage` call. Once
Buddy charges for cloud use, the policy check becomes an entitlement check
(an active plan with remaining hours) instead of a group grant, and these
records feed the invoices.

## Interface

- **Settings → Machines**, when granted: a **Cloud computer** card with
  Create, Start or Stop, status, this month's hours and a link to its
  workspace in Projects.
- **Admin Settings → AI plans**: the policy above, and cloud Runners in **Everyone's
  computers**, marked as cloud, with their usage.

## Open questions

- Where containers run first: Docker on the owner's PC with gVisor, or a cloud
  provider.
- How images are updated without interrupting a running turn.
- Whether workspace volumes are backed up, and for how long they are kept
  after a grant ends.

## Build order

1. Runner image and the manager with create, start, stop and delete.
2. `kind: 'cloud'` machines, created through the manager; admin-only grant.
3. Idle stop and start on demand.
4. Metering records and the usage view.
5. Billing, when Buddy has paid plans.
