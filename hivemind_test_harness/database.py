"""
In-memory ClientDatabase that avoids any plugin loading or disk I/O.
Implements the same public interface as hivemind_core.database.ClientDatabase
so it can be passed directly to HiveMindListenerProtocol.
"""
from typing import List, Optional, Iterable

from hivemind_plugin_manager.database import Client


class InMemoryClientDatabase:
    """Drop-in replacement for ClientDatabase backed by a plain dict."""

    def __init__(self):
        self._clients: dict[str, Client] = {}  # keyed by api_key

    # --- write API ---

    def add_client(self,
                   name: str,
                   key: str = "",
                   admin: bool = False,
                   intent_blacklist: Optional[List[str]] = None,
                   skill_blacklist: Optional[List[str]] = None,
                   message_blacklist: Optional[List[str]] = None,
                   allowed_types: Optional[List[str]] = None,
                   crypto_key: Optional[str] = None,
                   password: Optional[str] = None,
                   can_escalate: bool = True,
                   can_propagate: bool = True,
                   can_broadcast: bool = True) -> bool:
        if crypto_key is not None:
            crypto_key = crypto_key[:16]
        existing = self.get_client_by_api_key(key)
        if existing:
            if name:
                existing.name = name
            if intent_blacklist is not None:
                existing.intent_blacklist = intent_blacklist
            if skill_blacklist is not None:
                existing.skill_blacklist = skill_blacklist
            if message_blacklist is not None:
                existing.message_blacklist = message_blacklist
            if allowed_types is not None:
                existing.allowed_types = allowed_types
            existing.is_admin = admin
            if crypto_key:
                existing.crypto_key = crypto_key
            if password:
                existing.password = password
            existing.can_escalate = can_escalate
            existing.can_propagate = can_propagate
            existing.can_broadcast = can_broadcast
            self._clients[key] = existing
            return True

        client = Client(
            api_key=key,
            name=name,
            client_id=self.total_clients() + 1,
            is_admin=admin,
            intent_blacklist=intent_blacklist,
            skill_blacklist=skill_blacklist,
            message_blacklist=message_blacklist,
            allowed_types=allowed_types,
            crypto_key=crypto_key,
            password=password,
            can_escalate=can_escalate,
            can_propagate=can_propagate,
            can_broadcast=can_broadcast,
        )
        self._clients[key] = client
        return True

    def update_item(self, client: Client) -> bool:
        self._clients[client.api_key] = client
        return True

    def delete_client(self, key: str) -> bool:
        if key in self._clients:
            # mark revoked (don't reuse client_id)
            c = self._clients[key]
            self._clients[key] = Client(client_id=c.client_id, api_key="revoked")
            return True
        return False

    # --- read API ---

    def get_client_by_api_key(self, api_key: str) -> Optional[Client]:
        return self._clients.get(api_key)

    def get_clients_by_name(self, name: str) -> List[Client]:
        return [c for c in self._clients.values() if c.name == name]

    def total_clients(self) -> int:
        return len(self._clients)

    # --- lifecycle ---

    def sync(self):
        pass  # nothing to reload from disk

    def __enter__(self):
        return self

    def __exit__(self, _type, value, traceback):
        pass  # nothing to commit

    def __iter__(self) -> Iterable[Client]:
        return iter(list(self._clients.values()))

    def __len__(self) -> int:
        return len(self._clients)
