"""Advance a real API project to the next human gate; never patch project state."""
import argparse
import json
import time
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--project-id')
    parser.add_argument('--action', default='advance', choices=['advance', 'approve', 'prepare', 'execute', 'approve-search'])
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    def api(method, path, payload=None):
        response = session.request(method, args.base_url+path, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()
    pid = args.project_id
    if not pid:
        pid = api('POST', '/api/research/start', {
            'objective': '研究纯数学模型 dx/dt=-a*x 在 x(0)=5、t=0到8、a=0.3和0.8下的衰减规律，比较半衰期。用数值求解并与解析解交叉验证，输出轨迹图、CSV及最大绝对误差。明确这是数学模拟，不是观测或实体实验，不需要上传实测数据。只讨论此方程的数值正确性，不外推实际系统。一次计算通过误差验证即可停止。',
            'planning_only': False, 'reproducibility_seed': 839, 'max_iterations': 2,
        })['project_id']
    print(json.dumps({'project_id': pid}), flush=True)
    root = '/api/research/'+pid
    if args.action == 'approve':
        package = api('GET',root+'/review-package')
        api('POST',root+'/approve',{'acknowledged': True,'expected_versions':package['artifact_versions']})
    elif args.action == 'approve-search':
        api('POST',root+'/search-plan/approve',{'auto_approve_future':True})
    operation = args.action if args.action in {'prepare','execute'} else None
    for _ in range(40):
        project = api('GET',root)
        print(json.dumps({'phase': project['phase'], 'budget':project['budget']['used_model_calls'],
                          'computation_status':project.get('computation',{}).get('status')}, ensure_ascii=False), flush=True)
        if operation:
            payload = {'plan_id':project['computation']['plan_id']} if operation=='execute' else {}
            job = api('POST',root+'/computation/'+operation,payload)
        else:
            if project['phase'] in {'HUMAN_APPROVAL','SEARCH_PLAN_REVIEW','HUMAN_SOURCE_REVIEW',
                                    'HUMAN_REVISION_REVIEW','HUMAN_INTERVENTION_REQUIRED',
                                    'EXECUTION_WAITING','COMPLETED','FAILED','CANCELLED'}:
                print('HUMAN_GATE '+project['phase'],flush=True)
                return
            job=api('POST',root+'/step_async')
        while True:
            state=api('GET','/api/research/jobs/'+job['job_id'])
            if state['status'] in {'completed','failed'}:
                if state['status']=='failed':
                    print(json.dumps(state['error'],ensure_ascii=False),flush=True)
                    raise RuntimeError('Workflow job failed; inspect saved evidence')
                break
            time.sleep(2)
        if operation:
            print(json.dumps(state['result'],ensure_ascii=False),flush=True)
            return


if __name__=='__main__':
    main()
