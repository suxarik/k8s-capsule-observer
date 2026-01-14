"""
Kubernetes Operator for Tenant Observer
Exports metrics in Prometheus format.
"""

import logging
import threading
import json
from decimal import Decimal, ROUND_HALF_UP
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Any
from datetime import datetime

import kubernetes
import kopf

OPERATOR_NAME = "tenant-observer"
NAMESPACE = "tenant-observer"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(OPERATOR_NAME)


def to_decimal(value) -> Decimal:
    """Safe conversion to Decimal"""
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    try:
        return Decimal(str(value))
    except:
        return Decimal('0')


def parse_cpu(cpu_str: str) -> Decimal:
    if not cpu_str:
        return Decimal('0')
    cpu_str = cpu_str.strip()
    if cpu_str.endswith('m'):
        return Decimal(cpu_str[:-1]) / Decimal('1000')
    return Decimal('0')


def parse_memory(mem_str: str) -> Decimal:
    if not mem_str:
        return Decimal('0')
    mem_str = mem_str.strip()
    multipliers = {'Ki': 1024, 'Mi': 1024**2, 'Gi': 1024**3, 'Ti': 1024**4}
    for suffix, mult in multipliers.items():
        if mem_str.endswith(suffix):
            try:
                return Decimal(mem_str[:-len(suffix)]) * mult
            except:
                return Decimal('0')
    return Decimal('0')


def format_cpu(cores: Decimal) -> str:
    cores = to_decimal(cores)
    if cores >= 1:
        return f"{float(cores):.2f} cores"
    return f"{int(cores * 1000)}m"


def format_memory(bytes_val: Decimal) -> str:
    bytes_val = to_decimal(bytes_val)
    if bytes_val >= 1024**4:
        return f"{float(bytes_val / 1024**4):.2f} Ti"
    if bytes_val >= 1024**3:
        return f"{float(bytes_val / 1024**3):.2f} Gi"
    if bytes_val >= 1024**2:
        return f"{float(bytes_val / 1024**2):.2f} Mi"
    if bytes_val >= 1024:
        return f"{float(bytes_val / 1024):.2f} Ki"
    return f"{int(bytes_val)} B"


class PrometheusMetrics:
    def __init__(self):
        self.gauges = {}
    
    def _labels(self, d: Dict) -> tuple:
        return tuple(sorted((k, v.replace('.', '_').replace('-', '_')) for k, v in d.items()))
    
    def set(self, name: str, value: float, labels: Dict = None):
        key = (name.replace('-', '_'), self._labels(labels or {}))
        self.gauges[key] = value
    
    def tenant_namespace_count(self, tenant: str, count: int):
        self.set('tenant_observer_namespace_count', float(count), {'tenant': tenant})
    
    def tenant_cpu_requested(self, tenant: str, cores: float):
        self.set('tenant_observer_cpu_requested_cores', cores, {'tenant': tenant})
    
    def tenant_cpu_limit(self, tenant: str, cores: float):
        self.set('tenant_observer_cpu_limit_cores', cores, {'tenant': tenant})
    
    def tenant_memory_requested(self, tenant: str, bytes_val: float):
        self.set('tenant_observer_memory_requested_bytes', bytes_val, {'tenant': tenant})
    
    def tenant_memory_limit(self, tenant: str, bytes_val: float):
        self.set('tenant_observer_memory_limit_bytes', bytes_val, {'tenant': tenant})
    
    def tenant_cpu_pct(self, tenant: str, pct: float):
        self.set('tenant_observer_cpu_requested_percentage', pct, {'tenant': tenant})
    
    def tenant_memory_pct(self, tenant: str, pct: float):
        self.set('tenant_observer_memory_requested_percentage', pct, {'tenant': tenant})
    
    def tenant_pods(self, tenant: str, state: str, count: int):
        self.set('tenant_observer_pods_count', float(count), {'tenant': tenant, 'state': state})
    
    def tenant_services(self, tenant: str, count: int):
        self.set('tenant_observer_services_count', float(count), {'tenant': tenant})
    
    def tenant_health(self, tenant: str, score: float, status: str):
        self.set('tenant_observer_health_score', score, {'tenant': tenant, 'status': status})
    
    def total_tenants(self, count: int):
        self.set('tenant_observer_total_tenants', float(count), {})
    
    def total_namespaces(self, count: int):
        self.set('tenant_observer_total_namespaces', float(count), {})
    
    def total_cpu(self, cores: float):
        self.set('tenant_observer_total_cpu_requested_cores', cores, {})
    
    def total_memory(self, bytes_val: float):
        self.set('tenant_observer_total_memory_requested_bytes', bytes_val, {})
    
    def total_pods(self, count: int):
        self.set('tenant_observer_total_pods', float(count), {})
    
    def cluster_health(self, score: float, status: str):
        self.set('tenant_observer_cluster_health_score', score, {'status': status})
    
    def generate(self) -> str:
        lines = ['# HELP tenant_observer_namespace_count Number of namespaces', '# TYPE tenant_observer_namespace_count gauge']
        for (name, labels), value in self.gauges.items():
            lbl = ''
            if labels:
                lbl = '{' + ','.join(f'{k}="{v}"' for k, v in labels) + '}'
            lines.append(f'{name}{lbl} {value}')
        lines.append(f'tenant_observer_build_info{{operator="{OPERATOR_NAME}"}} 1')
        return '\n'.join(lines)
    
    def clear(self):
        self.gauges.clear()


metrics = PrometheusMetrics()


_crd_client = None
_core_client = None


def crd():
    global _crd_client
    if _crd_client is None:
        _crd_client = kubernetes.client.CustomObjectsApi()
    return _crd_client


def core():
    global _core_client
    if _core_client is None:
        _core_client = kubernetes.client.CoreV1Api()
    return _core_client


def list_tenants() -> List[Dict]:
    try:
        r = crd().list_cluster_custom_object(group="capsule.clastix.io", version="v1beta1", plural="tenants")
        return r.get('items', []) if isinstance(r, dict) else []
    except:
        return []


def get_namespaces(tenant: str) -> List[str]:
    try:
        ns = core().list_namespace(label_selector=f"capsule.clastix.io/tenant={tenant}")
        return [n.metadata.name for n in ns.items]
    except:
        return []


def get_namespace_usage(ns_name: str) -> Dict:
    usage = {
        'cpu_req': Decimal('0'), 'cpu_lim': Decimal('0'),
        'mem_req': Decimal('0'), 'mem_lim': Decimal('0'),
        'pods': 0, 'pods_run': 0, 'pods_wait': 0, 'pods_fail': 0,
        'svc': 0, 'cm': 0, 'secret': 0
    }
    try:
        pods = core().list_namespaced_pod(namespace=ns_name)
        for pod in pods.items:
            phase = pod.status.phase
            if phase == 'Running':
                usage['pods_run'] += 1
            elif phase == 'Pending':
                usage['pods_wait'] += 1
            elif phase == 'Failed':
                usage['pods_fail'] += 1
            usage['pods'] += 1
            
            for c in pod.spec.containers:
                r = c.resources
                if r:
                    req = r.requests or {}
                    lim = r.limits or {}
                    usage['cpu_req'] += parse_cpu(req.get('cpu', '0'))
                    usage['cpu_lim'] += parse_cpu(lim.get('cpu', '0'))
                    usage['mem_req'] += parse_memory(req.get('memory', '0'))
                    usage['mem_lim'] += parse_memory(lim.get('memory', '0'))
    except:
        pass
    
    try:
        usage['svc'] = len(core().list_namespaced_service(namespace=ns_name).items)
        usage['cm'] = len(core().list_namespaced_config_map(namespace=ns_name).items)
        usage['secret'] = len(core().list_namespaced_secret(namespace=ns_name).items)
    except:
        pass
    
    return usage


def gather(tenant: Dict) -> Dict:
    name = tenant.get('metadata', {}).get('name', 'unknown')
    spec = tenant.get('spec', {})
    namespaces = get_namespaces(name)
    
    total = {
        'cpu_req': Decimal('0'), 'cpu_lim': Decimal('0'),
        'mem_req': Decimal('0'), 'mem_lim': Decimal('0'),
        'pods': 0, 'pods_run': 0, 'pods_wait': 0, 'pods_fail': 0,
        'svc': 0, 'cm': 0, 'secret': 0
    }
    
    for ns in namespaces:
        u = get_namespace_usage(ns)
        for k in total:
            total[k] += u[k]
    
    cpu_pct = 0
    if total['cpu_lim'] > 0:
        cpu_pct = float(total['cpu_req'] / total['cpu_lim'] * 100)
    
    mem_pct = 0
    if total['mem_lim'] > 0:
        mem_pct = float(total['mem_req'] / total['mem_lim'] * 100)
    
    health_score = 100
    if cpu_pct > 0:
        health_score -= min(cpu_pct, 100) * 0.3
    if mem_pct > 0:
        health_score -= min(mem_pct, 100) * 0.3
    health_score -= (total['pods_wait'] + total['pods_fail']) * 10
    health_score = max(0, min(100, health_score))
    
    health_status = 'healthy' if health_score >= 90 else 'warning' if health_score >= 70 else 'critical'
    
    return {
        'name': name,
        'namespaces': namespaces,
        'namespace_count': len(namespaces),
        'cpu_req': float(total['cpu_req']),
        'cpu_lim': float(total['cpu_lim']),
        'mem_req': float(total['mem_req']),
        'mem_lim': float(total['mem_lim']),
        'cpu_pct': cpu_pct,
        'mem_pct': mem_pct,
        'pods': total['pods'],
        'pods_run': total['pods_run'],
        'pods_wait': total['pods_wait'],
        'pods_fail': total['pods_fail'],
        'svc': total['svc'],
        'cm': total['cm'],
        'secret': total['secret'],
        'health_score': health_score,
        'health_status': health_status,
        'owners': spec.get('owners', [])
    }


def update_metrics(tenants: List[Dict]):
    metrics.clear()
    
    total_cpu = Decimal('0')
    total_mem = Decimal('0')
    total_pods = 0
    health_scores = []
    
    for t in tenants:
        metrics.tenant_namespace_count(t['name'], t['namespace_count'])
        metrics.tenant_cpu_requested(t['name'], t['cpu_req'])
        metrics.tenant_cpu_limit(t['name'], t['cpu_lim'])
        metrics.tenant_memory_requested(t['name'], t['mem_req'])
        metrics.tenant_memory_limit(t['name'], t['mem_lim'])
        metrics.tenant_cpu_pct(t['name'], t['cpu_pct'])
        metrics.tenant_memory_pct(t['name'], t['mem_pct'])
        metrics.tenant_pods(t['name'], 'total', t['pods'])
        metrics.tenant_pods(t['name'], 'running', t['pods_run'])
        metrics.tenant_pods(t['name'], 'pending', t['pods_wait'])
        metrics.tenant_pods(t['name'], 'failed', t['pods_fail'])
        metrics.tenant_services(t['name'], t['svc'])
        metrics.tenant_health(t['name'], t['health_score'], t['health_status'])
        
        total_cpu += to_decimal(t['cpu_req'])
        total_mem += to_decimal(t['mem_req'])
        total_pods += t['pods']
        health_scores.append(t['health_score'])
    
    metrics.total_tenants(len(tenants))
    metrics.total_namespaces(sum(t['namespace_count'] for t in tenants))
    metrics.total_cpu(float(total_cpu))
    metrics.total_memory(float(total_mem))
    metrics.total_pods(total_pods)
    
    avg_health = sum(health_scores) / len(health_scores) if health_scores else 100
    status = 'healthy' if avg_health >= 90 else 'warning' if avg_health >= 70 else 'critical'
    metrics.cluster_health(avg_health, status)


def aggregate() -> Dict:
    tenants = list_tenants()
    
    if not tenants:
        update_metrics([])
        sync_tenant_infos([])
        return {'total_tenants': 0, 'tenants': [], 'summary': {}}
    
    data = [gather(t) for t in tenants]
    update_metrics(data)
    sync_tenant_infos(data)
    
    total_cpu = sum(to_decimal(t['cpu_req']) for t in data)
    total_mem = sum(to_decimal(t['mem_req']) for t in data)
    total_pods = sum(t['pods'] for t in data)
    avg_health = sum(t['health_score'] for t in data) / len(data)
    
    return {
        'timestamp': datetime.now().isoformat(),
        'total_tenants': len(tenants),
        'tenants': data,
        'summary': {
            'total_namespaces': sum(t['namespace_count'] for t in data),
            'total_cpu': format_cpu(total_cpu),
            'total_memory': format_memory(total_mem),
            'total_pods': total_pods,
            'health_score': float(avg_health)
        }
    }


def sync_tenant_infos(tenants_data: List[Dict]):
    """Create or update TenantInfo CR for each tenant - available via kubectl get ti"""
    operator_ns = "tenant-observer"
    
    for t in tenants_data:
        name = t['name']
        ti_name = f"{name}-info"
        
        ti_data = {
            'apiVersion': 'operators.clastix.io/v1',
            'kind': 'TenantInfo',
            'metadata': {
                'name': ti_name,
                'namespace': operator_ns,
                'labels': {
                    'tenant': name
                }
            },
            'spec': {
                'name': name,
                'namespaces': t['namespaces'],
                'namespace_count': t['namespace_count'],
                'resources': {
                    'cpu': {
                        'requested': {'value': format_cpu(to_decimal(t['cpu_req'])), 'raw_milli': int(t['cpu_req']), 'percentage': round(t['cpu_pct'], 2), 'percentage_display': f"{t['cpu_pct']:.1f}%", 'status': 'ok'},
                        'limit': {'value': format_cpu(to_decimal(t['cpu_lim'])), 'raw_milli': int(t['cpu_lim'])},
                        'usage': {'value': format_cpu(to_decimal(t['cpu_req'])), 'raw_milli': int(t['cpu_req']), 'percentage': round(t['cpu_pct'], 2), 'percentage_display': f"{t['cpu_pct']:.1f}%"},
                        'utilization_efficiency': round(t['cpu_pct'], 2)
                    },
                    'memory': {
                        'requested': {'value': format_memory(to_decimal(t['mem_req'])), 'raw_bytes': int(t['mem_req']), 'percentage': round(t['mem_pct'], 2), 'percentage_display': f"{t['mem_pct']:.1f}%", 'status': 'ok'},
                        'limit': {'value': format_memory(to_decimal(t['mem_lim'])), 'raw_bytes': int(t['mem_lim'])},
                        'usage': {'value': format_memory(to_decimal(t['mem_req'])), 'raw_bytes': int(t['mem_req']), 'percentage': round(t['mem_pct'], 2), 'percentage_display': f"{t['mem_pct']:.1f}%"},
                        'utilization_efficiency': round(t['mem_pct'], 2)
                    },
                    'pods': {
                        'requested': t['pods'],
                        'running': t['pods_run'],
                        'pending': t['pods_wait'],
                        'failed': t['pods_fail'],
                        'percentage': f"{t['pods'] / max(t['pods'], 1) * 100:.0f}%",
                        'status': 'ok'
                    },
                    'storage': {'requested': {'value': '0', 'raw_bytes': 0}, 'limit': {'value': '0', 'raw_bytes': 0}},
                    'services': {'count': t['svc']},
                    'configmaps': {'count': t['cm']},
                    'secrets': {'count': t['secret']},
                    'ingresses': {'count': 0},
                    'health_score': {'score': t['health_score'], 'status': t['health_status'], 'details': {}}
                },
                'owners': t.get('owners', [])
            },
            'status': {
                'phase': 'Active',
                'state': t['health_status']
            },
            'health': {
                'score': t['health_score'],
                'status': t['health_status'],
                'details': {}
            }
        }
        
        try:
            # Try to get existing
            crd().get_namespaced_custom_object(
                group="operators.clastix.io",
                version="v1",
                namespace=operator_ns,
                plural="tenantinfos",
                name=ti_name
            )
            # Update existing
            crd().patch_namespaced_custom_object(
                group="operators.clastix.io",
                version="v1",
                namespace=operator_ns,
                plural="tenantinfos",
                name=ti_name,
                body=ti_data
            )
            logger.debug(f"Updated TenantInfo: {ti_name}")
        except:
            # Create new
            try:
                crd().create_namespaced_custom_object(
                    group="operators.clastix.io",
                    version="v1",
                    namespace=operator_ns,
                    plural="tenantinfos",
                    body=ti_data
                )
                logger.debug(f"Created TenantInfo: {ti_name}")
            except Exception as e:
                logger.warning(f"Failed to create TenantInfo {ti_name}: {e}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        elif self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(metrics.generate().encode())
        elif self.path == '/tenants':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(aggregate(), default=str).encode())
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_server(port=8080):
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()


@kopf.on.event('capsule.clastix.io', 'v1beta1', 'tenants')
def on_tenant_event(body, **kwargs):
    name = body.get('metadata', {}).get('name', 'unknown')
    logger.info(f"Event for tenant: {name}")
    aggregate()


if __name__ == '__main__':
    kubernetes.config.load_incluster_config()
    logger.info(f"Operator {OPERATOR_NAME} started")
    
    threading.Thread(target=run_server, daemon=True).start()
    
    kopf.run(
        registry=kopf.get_default_registry(),
        clusterwide=True,
        namespace=None,
    )