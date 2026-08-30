# BDSPy -- Bedrock Dedicated Server, Python

A Minecraft Bedrock Edition server written from scratch in pure Python.
No external dependencies -- stdlib only (`socket`, `struct`, `zlib`, `tomllib`).
Runs on Termux.

### NOTE: This Project is still on heavy development.

Target client version: **1.26.30** (protocol 1001).

## Running

```
python3 main.py
```

Edit `config.toml` for port, MOTD, ground layers, starting items, view radius.

## Logging

Colored, leveled logs (`log.py`) -- DEBUG shows every packet in/out with
sizes, split-packet reassembly progress, compression mode, and ordering
buffer activity, which is exactly the detail you want when chasing a
malformed-packet bug. Toggle via `config.toml`'s `[logging]` section:

```toml
[logging]
level = "DEBUG"  # or INFO / WARN / ERROR
```

Colors auto-disable when output isn't a terminal (e.g. redirected to a file),
so log files stay clean. WARN/ERROR go to stderr.

## Layout

```
main.py               entry point
log.py                 colored, leveled logging (DEBUG/INFO/WARN/ERROR)

network/
  udp.py              raw UDP socket wrapper (no protocol knowledge)
  raknet.py           RakNet offline handshake (ping/pong, connection open) + session lifecycle
  reliability.py      stateless datagram/frame encode-decode (RakNet wire format)
  session.py          per-connection state: reliable-ordered delivery, split
                       packet reassembly, game-packet batch (de)compression

protocol/
  varint.py           unsigned LEB128 + zigzag helpers
  reader.py           field-level packet reader
  writer.py           field-level packet writer
  types.py            Bedrock's NBT flavor (little-endian, varint-length strings)
  packets.py          packet ID table + builders/parsers for the whole join sequence

world/
  block.py            canonical block palette loader + the 5 placeable items
  biome.py            minimal biome table
  chunk.py            pure chunk-column data structure (no wire format)
  generator.py         flat world generator

server/
  server.py           orchestrates the join sequence, wires transport to game logic
  player.py           per-player join state
  entity.py           entity ID allocation + position tracking
  world.py            spawn point, generator + chunk encoder wiring
  chunk.py            encodes world.Chunk -> LevelChunk packet bytes (subchunk/biome encoding)

commands/
  dispatcher.py       minimal chat-command registry (not yet wired to a text handler)

data/
  canonical_block_states.nbt   the client's own block palette (pmmp/BedrockData, CC0-1.0)
```

## Status

**Verified end-to-end with a real Bedrock client implementation** (PrismarineJS's
`bedrock-protocol`, running its actual compiled 1.26.30 protocol codec against
our server over real loopback UDP -- not just our own self-consistency checks).
The full join sequence completes and fires `spawn`: network settings, login,
resource pack negotiation, StartGame (~2MB uncompressed, full block palette),
item registry, inventory, biomes, entities, 81 chunks, player spawn.

Three real bugs were found this way and fixed:

1. **`StartGame.block_properties` entry format** -- each entry needs to be a
   plain protocol string (`name`) followed by a *separate* NBT root tag
   (`state`), not one merged NBT compound containing both fields. Caught via
   `unexpected tag end` while the real parser read an NBT tag name.
2. **`item_registry`'s trailing NBT field** -- was hand-rolled placeholder
   bytes instead of a real empty NBT root tag (type byte + name + TAG_END).
3. **`ItemV4` empty/air stack encoding** -- `ItemV4` has no void shortcut for
   `network_id=0`; every field is still present, just zeroed. The 2-byte
   placeholder we had caused the reader to overrun the buffer.

Also fixed earlier via live device testing (see git history / prior notes):
MOTD protocol/version mismatch, and a split-packet reassembly bug that stalled
delivery ordering for anything sent right after a fragmented packet (e.g. `Login`).

### A subtlety worth knowing: lenient vs strict parsing

`bedrock-protocol` (the JS reference used above) validates *structure* --
can these bytes be read into the right shape? -- but not necessarily every
field's *semantics*. After the real client still failed past this point, we
cross-checked our `StartGame` field-by-field against `gophertunnel`
(Sandertv/gophertunnel, pinned to the exact tag matching protocol 1001 /
1.26.30) -- the Go library behind Dragonfly, a server real players connect to
with the actual game client daily. That caught one more real bug:

4. **`PlayerPermissions` encoding** -- it's a zigzag `Varint32`, not a plain
   `u8` like the value (2 = operator) might suggest. Same semantic value,
   different bytes on the wire. `bedrock-protocol`/`minecraft-data`'s schema
   didn't flag this; gophertunnel's version-pinned source did.
5. **Empty/air `ItemV4` stacks were still wrong.** The earlier "fix" (a fully
   zeroed but structurally complete `ItemV4`) passed `bedrock-protocol`'s
   generic schema validation, but a real device test still disconnected right
   after receiving `inventory_content` -- with every byte fully ACKed, ruling
   out packet loss. Cross-checking gophertunnel's actual `ItemInstanceNew`
   encoder showed the real client special-cases `network_id==0`: it writes
   *only* a zero-length marker after `block_runtime_id` and stops entirely --
   no has_nbt/can_place_on/can_destroy fields at all. `minecraft-data`'s
   generic schema has no such special case, which is exactly why the lenient
   test tool accepted a version that the strict real client didn't.

## Architecture: dispatch runs off the receive thread

A subtler bug surfaced after the fix above: the **same bytes, from the same
real device, succeeded on one connection attempt and failed on another** --
a strong signal of a timing issue, not a deterministic encoding bug. The
cause: game-packet dispatch (which can trigger large blocking sends, like
`StartGame`'s ~80KB compressed payload going out as 50+ fragments) ran
directly on the socket's receive thread. While that thread was busy sending,
it wasn't calling `recvfrom()` -- so the client's own packets (including
whatever it needed acknowledged) sat unread until the burst finished,
occasionally outlasting the client's patience.

Fixed by decoupling the two: `network/session.py` now enqueues decoded game
packets onto a queue instead of dispatching them inline, and
`network/raknet.py` runs a separate worker thread that drains that queue.
The receive thread now only does fast RakNet-level work (parse, split
reassembly, ordering, ACK) and is never blocked by how long the game layer
takes to respond.

**Verified this actually works**, not just in theory: wrote a test that
makes the game-dispatch handler sleep for a full second, then sends several
more datagrams *during* that sleep -- confirmed via logging that ACKs for
all of them go out immediately, well before the slow handler finishes. Also
reran the full live join sequence and the packet-loss simulation afterward
to confirm nothing regressed.

6. **Removed `available_entity_identifiers` (packet 119) entirely.** Studied
   Pumpkin (a real, actively-developed Rust server with working Bedrock
   support -- github.com/Pumpkin-MC/Pumpkin) as a third independent source.
   Its full packet module list has no implementation of this packet at all --
   it's simply never sent, and real clients connect to it fine. That's a
   strong signal it's optional (the client likely falls back to its own
   built-in vanilla entity list), so we stopped sending it too, removing a
   packet whose content we could never fully verify was correct.
   Cross-checking Pumpkin also *independently confirmed* three things we'd
   already fixed: the empty-stack special case (`id==0` -> just a
   zero-length marker, matching gophertunnel exactly), the non-empty item's
   10-byte extra-data structure, and `inventory_content`'s and
   `set_spawn_position`'s full field layouts -- all now verified against two
   independent real-client-tested implementations, not just one.

Also cross-verified against gophertunnel/Dragonfly: `Dimension`, `Generator`,
`Difficulty` enum values, `GameRules`/`Blocks` count-prefix types, that
`ServerBlockStateChecksum` is safe to leave at 0 (Dragonfly itself never sets
it), and the full field order/types of `set_spawn_position`,
`biome_definition_list`, and `item_registry` (all confirmed correct as-is).

## Known gaps

- No block placing/breaking yet (`inventory_transaction` / `player_action`
  aren't handled) -- items sit correctly in the hotbar with correct block
  IDs, but nothing places them yet
- Movement is unauthoritative (client-side prediction only, no server corrections)
- `commands/dispatcher.py` exists but isn't wired to a `text` packet handler yet
- The `resource_pack_client_response` status codes aren't inspected (any
  response advances to the next stage) -- harmless since we never require
  packs, but worth tightening eventually
- The initial RakNet handshake (ping/pong, Open Connection Request/Reply)
  has no server-side retry -- this matches real RakNet, where the *client*
  is expected to retry those first few packets, but it does mean severe
  loss (~20%+) right at connection time can still fail before a Session
  (and its retransmission) even exists

## Reliability: ACK/NAK-based retransmission

Added after a real device test over actual WiFi disconnected partway through
`StartGame` (a ~1.9MB uncompressed / ~80KB compressed payload, sent as ~55+
unpaced UDP fragments) with no clear error -- the server log showed the
client retransmitting a packet we'd already processed, meaning it hadn't
gotten our ACK in time. The server had no retransmission logic at all: if
any of those fragments got dropped by the network, the client would wait
forever for a piece that was never coming.

Fixed in `network/reliability.py` (ACK/NAK packet parsing) and
`network/session.py`:
- Every reliable datagram we send is tracked until acknowledged
- A background thread (`RakNetTransport._tick_loop`) resends anything unacked
  after 0.6s, up to 12 attempts
- Incoming datagram sequence gaps trigger an immediate NAK (faster recovery
  than waiting for the timeout)
- Incoming NAKs from the client trigger an immediate resend

**Validated with a real loss simulation**, not just theory: built a lossy
UDP proxy (`test_harness/lossy_proxy.py`, dev-only, not part of the shipped
server) that randomly drops a configurable percentage of packets in both
directions, and ran the full join sequence through it against the real
`bedrock-protocol` client:

| Simulated loss | Result |
|---|---|
| 10% | Spawned successfully, consistently (2/2 runs) |
| 15% | Spawned successfully once; timed out once (our test harness gives up after 15s -- at higher loss, recovery can take longer than that per-packet, since each resend waits up to 0.6s and can retry up to 12 times, but the mechanism itself keeps trying) |
| 25%+ | Failed at the initial handshake stage, before a Session exists -- see the gap noted above |

10% packet loss is already fairly severe for real WiFi, so this should
meaningfully help with exactly the kind of real-world flakiness you hit.

## License note

`data/canonical_block_states.nbt` is from `pmmp/BedrockData` (CC0-1.0).

## Aware
This project is 100% made with ai, im sorry if there's any bug somewhere in the code.

## Contributing
Feel free to Contribute (i need a programmer...)
