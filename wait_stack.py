#!/usr/bin/env python3
import subprocess, json, time, sys

stack_id = sys.argv[1] if len(sys.argv) > 1 else '408ff9a3-df41-4a16-b06b-7f8b9da18dcc'
region = 'cn-beijing'

print(f"等待 Stack {stack_id} 完成...")
for i in range(60):
    result = subprocess.run(
        ['aliyun', 'ros', 'GetStack', '--RegionId', region, '--StackId', stack_id],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    status = data.get('Status', '')
    reason = data.get('StatusReason', '')
    print(f'[{i*30}s] Status: {status} - {reason}')
    
    if 'COMPLETE' in status or 'FAILED' in status or 'ROLLBACK' in status:
        print('达到终态!')
        if status == 'CREATE_COMPLETE':
            # 打印 Outputs
            outputs = data.get('Outputs', [])
            if outputs:
                print("\nStack Outputs:")
                for o in outputs:
                    print(f"  {o.get('OutputKey')}: {o.get('OutputValue')}")
        break
    
    time.sleep(30)
