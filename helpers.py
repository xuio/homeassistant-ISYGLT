import logging
from typing import List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

from homeassistant.components.modbus.const import (
    MODBUS_DOMAIN,
    CALL_TYPE_REGISTER_HOLDING,
    CALL_TYPE_WRITE_REGISTERS,
)

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Any

_LOGGER = logging.getLogger(__name__)

# ---------------- Priority Modbus Queue -----------------


@dataclass(order=True)
class HubCommand:
    priority: int
    _counter: int = field(compare=True)
    op: str = field(compare=False)
    address: int = field(compare=False)
    length: int = field(compare=False)
    values: List[int] | None = field(compare=False, default=None)
    future: asyncio.Future = field(compare=False, default_factory=asyncio.Future)


class ModbusQueue:
    """Priority queue per hub with simple read deduplication."""

    def __init__(self, hass: HomeAssistant, mixin: "IsyGltModbusMixin") -> None:
        self.hass = hass
        self._hub_name = mixin._hub_name
        self._mixin = mixin
        self._queue: asyncio.PriorityQueue[HubCommand] = asyncio.PriorityQueue()
        self._counter = 0
        self._pending_reads: dict[tuple[int, int], HubCommand] = {}
        self._task = hass.loop.create_task(self._worker())

    def _next_counter(self) -> int:
        self._counter += 1
        return self._counter

    async def _worker(self):
        """Process commands one at a time serially."""
        while True:
            cmd: HubCommand = await self._queue.get()
            try:
                if cmd.op == "read":
                    result = await self._mixin._direct_read(cmd.address, cmd.length)
                    if not cmd.future.done():
                        cmd.future.set_result(result)
                else:  # write
                    await self._mixin._direct_write(cmd.address, cmd.values or [])
                    if not cmd.future.done():
                        cmd.future.set_result(True)
            except Exception as exc:  # noqa: BLE001
                if not cmd.future.done():
                    cmd.future.set_exception(exc)
            finally:
                if cmd.op == "read":
                    self._pending_reads.pop((cmd.address, cmd.length), None)
                self._queue.task_done()

    def enqueue_read(self, address: int, length: int, priority: int) -> asyncio.Future:
        key = (address, length)
        if key in self._pending_reads:
            return self._pending_reads[key].future
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        cmd = HubCommand(priority, self._next_counter(), "read", address, length, None, fut)
        self._pending_reads[key] = cmd
        self._queue.put_nowait(cmd)
        return fut

    def enqueue_write(self, address: int, values: List[int], priority: int) -> asyncio.Future:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        cmd = HubCommand(priority, self._next_counter(), "write", address, len(values), values, fut)
        self._queue.put_nowait(cmd)
        return fut

# --------------------------------------------------------


class IsyGltModbusMixin:
    """Mixin that provides helper methods for reading/writing Modbus registers via the HA Modbus hub."""

    def __init__(self, hass: HomeAssistant, hub_name: str):
        self.hass = hass
        self._hub_name = hub_name
        # Cache config entry id linkage
        self._config_entry_id: str | None = None
        # bulk cache
        self._cache_data: list[int] | None = None
        self._cache_ts: float = 0.0

        # Create per-hub lock to prevent concurrent Modbus calls
        if not hasattr(IsyGltModbusMixin, "_hub_queues"):
            IsyGltModbusMixin._hub_queues = {}
        if hub_name not in IsyGltModbusMixin._hub_queues:
            IsyGltModbusMixin._hub_queues[hub_name] = ModbusQueue(hass, self)
        self._queue: ModbusQueue = IsyGltModbusMixin._hub_queues[hub_name]

    # ---------- device registry helper ----------

    def ensure_device_entry(self, base_id: str, name: str, model: str):
        """Create or fetch device registry entry for this entity."""
        from homeassistant.helpers import device_registry as dr

        dev_reg = dr.async_get(self.hass)

        return dev_reg.async_get_or_create(
            identifiers={(DOMAIN, base_id)},
            manufacturer="ISYGLT",
            name=name,
            model=model,
        )

    @property
    def config_entry_id(self) -> str | None:
        """Try to find the Modbus config entry id matching our hub."""
        if self._config_entry_id is not None:
            return self._config_entry_id

        modbus_entries = self.hass.config_entries.async_entries("modbus")

        # Try exact match on hub name in title or data
        for entry in modbus_entries:
            if entry.title == self._hub_name or entry.data.get("name") == self._hub_name:
                self._config_entry_id = entry.entry_id
                return self._config_entry_id

        # Fallback: first Modbus entry if available
        if modbus_entries:
            self._config_entry_id = modbus_entries[0].entry_id
        return self._config_entry_id

    @property
    def hub(self):
        return self.hass.data[MODBUS_DOMAIN][self._hub_name]

    async def async_read_registers(self, address: int, count: int = 1) -> List[int] | None:
        """Read holding registers via cached bulk read.

        Uses the public `async_pb_call` helper introduced in recent HA
        releases. Returns a list of 16-bit register values or `None` on
        failure.
        """

        poll_interval = self.hass.data.get(DOMAIN, {}).get("poll_interval", 1.0)
        BLOCK_SIZE = 120  # safe chunk size <= 125

        result_regs: list[int] = []
        remaining = count
        cur_addr = address

        if not hasattr(self, "_block_cache"):
            self._block_cache = {}

        cache: dict[int, tuple[float, list[int]]] = self._block_cache  # start_addr -> (ts, data)

        ranges = self.hass.data.get(DOMAIN, {}).get("bulk_range", {}).get(self._hub_name, [])

        # Priority 1 for normal reads
        fut = self._queue.enqueue_read(address, count, priority=1)
        data = await fut
        return data

    # ---------------- direct low-level read/write (internal) -------------

    async def _direct_read(self, address: int, count: int) -> List[int] | None:
        """Perform actual Modbus read (protected by queue)."""
        # existing logic moved here simplified single block read
        result = await self.hub.async_pb_call(
            unit=None,
            address=address,
            value=count,
            use_call=CALL_TYPE_REGISTER_HOLDING,
        )
        if not result or getattr(result, "registers", None) is None:
            return None
        return result.registers

    async def _direct_write(self, address: int, values: List[int]):
        await self.hub.async_pb_call(
            unit=None,
            address=address,
            value=values,
            use_call=CALL_TYPE_WRITE_REGISTERS,
        )
        return

    async def async_write_registers(self, address: int, values: List[int]):
        """Write multiple holding registers via the Modbus hub."""

        # priority 0 for state changes
        await self._queue.enqueue_write(address, values, priority=0)

        # Patch cache for each word written
        if hasattr(self, "_block_cache"):
            BLOCK_SIZE = 120
            for idx, val in enumerate(values):
                addr = address + idx
                block_start = (addr // BLOCK_SIZE) * BLOCK_SIZE
                if block_start in self._block_cache:
                    ts, data = self._block_cache[block_start]
                    offset = addr - block_start
                    if offset < len(data):
                        data[offset] = val & 0xFFFF
                        self._block_cache[block_start] = (ts, data)

        now_time = time.monotonic()

        # record write time per hub to help throttling reads
        self.hass.data.setdefault(DOMAIN, {}).setdefault("last_write", {})[
            self._hub_name
        ] = now_time

        # throttle bulk reads
        poll_int = self.hass.data.get(DOMAIN, {}).get("poll_interval", 1.0)
        self.hass.data[DOMAIN].setdefault("bulk_throttle", {})[self._hub_name] = now_time + poll_int

        # schedule delayed dispatcher
        PROP_DELAY = poll_int + poll_int  # two cycles

        self.hass.loop.call_later(
            PROP_DELAY,
            lambda: async_dispatcher_send(self.hass, "isyglt_reg_updated"),
        ) 