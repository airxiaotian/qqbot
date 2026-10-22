import unittest

from server import device_allowed, is_tailscale_ipv4


class AccessTest(unittest.TestCase):
    def test_only_tailscale_cgNAT(self):
        self.assertTrue(is_tailscale_ipv4("100.64.0.1"))
        self.assertTrue(is_tailscale_ipv4("100.127.255.255"))
        self.assertFalse(is_tailscale_ipv4("100.128.0.1"))
        self.assertFalse(is_tailscale_ipv4("100.63.255.255"))
        self.assertFalse(is_tailscale_ipv4("192.168.1.8"))
        self.assertFalse(is_tailscale_ipv4("8.8.8.8"))

    def test_empty_allowlist_rejects_everyone(self):
        info = {"id": "n1", "hostname": "mbp", "dns": "mbp.tailnet.ts.net"}
        self.assertFalse(device_allowed(info, {"allowed_hostnames": [], "allowed_node_ids": []}))

    def test_hostname_and_node_id(self):
        info = {"id": "n1", "hostname": "mbp", "dns": "mbp.tailnet.ts.net."}
        config = {"allowed_hostnames": ["MBP"], "allowed_node_ids": []}
        self.assertTrue(device_allowed(info, config))
        self.assertTrue(device_allowed(info, {"allowed_hostnames": [], "allowed_node_ids": ["n1"]}))
        self.assertFalse(device_allowed(info, {"allowed_hostnames": ["phone"], "allowed_node_ids": []}))


if __name__ == "__main__":
    unittest.main()
