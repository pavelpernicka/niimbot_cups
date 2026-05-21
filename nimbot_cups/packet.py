class NimbotPacket:
    def __init__(self, packet_type: int, data: bytes):
        self.packet_type = packet_type
        self.data = data

    @classmethod
    def from_bytes(cls, payload: bytes) -> "NimbotPacket":
        if payload[:2] != b"\x55\x55" or payload[-2:] != b"\xaa\xaa":
            raise ValueError("bad packet framing")
        packet_type = payload[2]
        length = payload[3]
        data = payload[4 : 4 + length]
        checksum = packet_type ^ length
        for byte in data:
            checksum ^= byte
        if checksum != payload[-3]:
            raise ValueError("bad packet checksum")
        return cls(packet_type, data)

    def to_bytes(self) -> bytes:
        checksum = self.packet_type ^ len(self.data)
        for byte in self.data:
            checksum ^= byte
        return bytes(
            (
                0x55,
                0x55,
                self.packet_type,
                len(self.data),
                *self.data,
                checksum,
                0xAA,
                0xAA,
            )
        )
