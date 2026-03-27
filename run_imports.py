import importlib, sys, traceback
sys.path.insert(0, '.')
modules=['modbus.rtu_client','modbus.tcp_client','modbus.manager','utils.parser','utils.converter','utils.logger']
for m in modules:
    try:
        importlib.import_module(m)
        print('OK', m)
    except Exception:
        print('ERR', m)
        traceback.print_exc()
