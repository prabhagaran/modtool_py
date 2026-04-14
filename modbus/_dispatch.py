"""
modbus/_dispatch.py
───────────────────
Shared Modbus function-code dispatcher used by both RTUClient and TCPClient.
Extracted here so tcp_client no longer needs to import from rtu_client.
"""


def _dispatch(client, fc: int, address: int, slave_id: int,
              count: int, values):
    """Route the call to the correct pymodbus client method."""
    fc = int(fc)

    def _invoke(method, kwargs: dict, sid: int):
        """Call *method* with several calling conventions to support
        multiple pymodbus versions and client implementations.

        Order tried:
          1. keyword ``slave=`` (pymodbus 3.x)
          2. keyword ``unit=``  (pymodbus 2.x)
          3. positional (older styles)

        Any TypeError from incompatible signature is caught and the next
        convention is tried. Other exceptions propagate normally.
        """
        # 1) try `slave=`
        try:
            return method(**{**kwargs, "slave": sid})
        except TypeError:
            pass

        # 2) try `unit=`
        try:
            return method(**{**kwargs, "unit": sid})
        except TypeError:
            pass

        # 3) try positional calling conventions
        try:
            pos = []
            if "address" in kwargs:
                pos.append(kwargs["address"])
            if "count" in kwargs:
                pos.append(kwargs["count"])
            if "value" in kwargs:
                pos.append(kwargs["value"])
            # Append unit as final positional
            pos.append(sid)
            return method(*pos)
        except TypeError:
            pass

        # Give up and re-raise from original kwargs for easier debugging.
        return method(**{**kwargs})

    if   fc == 1:
        return _invoke(client.read_coils,
                       dict(address=address, count=count), slave_id)
    elif fc == 2:
        return _invoke(client.read_discrete_inputs,
                       dict(address=address, count=count), slave_id)
    elif fc == 3:
        return _invoke(client.read_holding_registers,
                       dict(address=address, count=count), slave_id)
    elif fc == 4:
        return _invoke(client.read_input_registers,
                       dict(address=address, count=count), slave_id)
    elif fc == 5:
        val = bool(values[0]) if values else False
        return _invoke(client.write_coil,
                       dict(address=address, value=val), slave_id)
    elif fc == 6:
        val = int(values[0]) & 0xFFFF if values else 0
        return _invoke(client.write_register,
                       dict(address=address, value=val), slave_id)
    elif fc == 15:
        vals = [bool(v) for v in values] if values else [False]
        return _invoke(client.write_coils,
                       dict(address=address, values=vals), slave_id)
    elif fc == 16:
        vals = [int(v) & 0xFFFF for v in values] if values else [0]
        return _invoke(client.write_registers,
                       dict(address=address, values=vals), slave_id)
    else:
        raise ValueError(f"Unsupported function code: {fc}")
