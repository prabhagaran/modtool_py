import argparse
from pymodbus.client.sync import ModbusTcpClient

# Register offsets (0-based)
OFF = {
    'DC_V': 0,
    'DC_I': 1,
    'AC_V': 2,
    'AC_I': 3,
    'PWR': 4,
    'FREQ': 5,
    'STATE': 9,
    'FAULT': 10,
    'ENERGY': 19,
}


def read_all(client, unit=1):
    rr = client.read_holding_registers(0, 30, unit=unit)
    if not rr or hasattr(rr, 'isError') and rr.isError():
        print('Read error or no response')
        return
    regs = rr.registers
    print('DC Voltage:', regs[OFF['DC_V']])
    print('DC Current:', regs[OFF['DC_I']])
    print('AC Voltage:', regs[OFF['AC_V']])
    print('AC Current:', regs[OFF['AC_I']])
    print('Power (kW):', regs[OFF['PWR']])
    print('Frequency (Hz):', regs[OFF['FREQ']] / 100.0)
    print('State:', regs[OFF['STATE']])
    print('Fault Code:', regs[OFF['FAULT']])
    print('Energy (kWh):', regs[OFF['ENERGY']])


def main():
    p = argparse.ArgumentParser(description='Read inverter registers via Modbus TCP')
    p.add_argument('--host', '-H', default='127.0.0.1')
    p.add_argument('--port', '-P', type=int, default=1502)
    p.add_argument('--unit', '-u', type=int, default=1)
    args = p.parse_args()

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        print('Unable to connect to Modbus server at', args.host, args.port)
        return
    try:
        read_all(client, unit=args.unit)
    finally:
        client.close()


if __name__ == '__main__':
    main()
