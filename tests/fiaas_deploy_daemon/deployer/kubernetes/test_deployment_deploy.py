#!/usr/bin/env python
# -*- coding: utf-8

import mock
import pytest
from mock import create_autospec
from requests import Response

from fiaas_deploy_daemon.config import Configuration
from fiaas_deploy_daemon.deployer.kubernetes.deployment import _make_probe, DeploymentDeployer
from fiaas_deploy_daemon.specs.models import CheckSpec, HttpCheckSpec, TcpCheckSpec, PrometheusSpec, ConfigMapSpec, \
    AutoscalerSpec, ResourceRequirementSpec, ResourcesSpec

SELECTOR = {'app': 'testapp'}
LABELS = {"deployment_deployer": "pass through"}
DEPLOYMENTS_URI = '/apis/extensions/v1beta1/namespaces/default/deployments/'


def test_make_http_probe():
    check_spec = CheckSpec(http=HttpCheckSpec(path="/", port=8080,
                                              http_headers={"Authorization": "ZmlubjpqdXN0aW5iaWViZXJfeG94bw=="}),
                           tcp=None, execute=None, initial_delay_seconds=30, period_seconds=60, success_threshold=3,
                           timeout_seconds=10)
    probe = _make_probe(check_spec)
    assert probe.httpGet.path == "/"
    assert probe.httpGet.port == 8080
    assert probe.httpGet.scheme == "HTTP"
    assert len(probe.httpGet.httpHeaders) == 1
    assert probe.httpGet.httpHeaders[0].name == "Authorization"
    assert probe.httpGet.httpHeaders[0].value == "ZmlubjpqdXN0aW5iaWViZXJfeG94bw=="
    assert probe.initialDelaySeconds == 30
    assert probe.periodSeconds == 60
    assert probe.successThreshold == 3
    assert probe.timeoutSeconds == 10


def test_make_tcp_probe():
    check_spec = CheckSpec(tcp=TcpCheckSpec(port=31337), http=None, execute=None, initial_delay_seconds=30,
                           period_seconds=60, success_threshold=3, timeout_seconds=10)
    probe = _make_probe(check_spec)
    assert probe.tcpSocket.port == 31337
    assert probe.initialDelaySeconds == 30
    assert probe.periodSeconds == 60
    assert probe.successThreshold == 3
    assert probe.timeoutSeconds == 10


def test_make_probe_should_fail_when_no_healthcheck_is_defined():
    check_spec = CheckSpec(tcp=None, execute=None, http=None, initial_delay_seconds=30, period_seconds=60,
                           success_threshold=3, timeout_seconds=10)
    with pytest.raises(RuntimeError):
        _make_probe(check_spec)


class TestDeploymentDeployer(object):
    @pytest.fixture(params=("diy", "gke"))
    def infra(self, request):
        yield request.param

    @pytest.fixture(params=({}, {'A_GLOBAL_DIGIT': '0.01', 'A_GLOBAL_STRING': 'test'},
                            {'A_GLOBAL_DIGIT': '0.01', 'A_GLOBAL_STRING': 'test',
                             'INFRASTRUCTURE': 'illegal', 'ARTIFACT_NAME': 'illegal'}))
    def global_env(self, request):
        yield request.param

    @pytest.fixture
    def deployer(self, infra, global_env):
        config = mock.create_autospec(Configuration([]), spec_set=True)
        config.infrastructure = infra
        config.environment = "test"
        config.global_env = global_env
        return DeploymentDeployer(config)

    @pytest.mark.parametrize("volume,envs", [
        (False, False),
        (True, False),
        (False, True),
        (True, True)
    ])
    def test_deploy_new_deployment(self, infra, global_env, post, deployer, app_spec, volume, envs):
        app_spec = app_spec._replace(config=ConfigMapSpec(volume, ["ENV"] if envs else []))
        deployer.deploy(app_spec, SELECTOR, LABELS)

        expected_volumes = []
        expected_volume_mounts = []
        if volume:
            expected_volumes = [{
                'name': app_spec.name,
                'configMap': {
                    'name': app_spec.name
                }}]
            expected_volume_mounts = [{
                'name': app_spec.name,
                'readOnly': True,
                'mountPath': '/var/run/config/fiaas/'
            }]

        expected_deployment = {
            'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS),
            'spec': {
                'selector': {'matchLabels': SELECTOR},
                'template': {
                    'spec': {
                        'dnsPolicy': 'ClusterFirst',
                        'serviceAccountName': 'fiaas-no-access',
                        'restartPolicy': 'Always',
                        'volumes': expected_volumes,
                        'imagePullSecrets': [],
                        'containers': [{
                            'livenessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'tcpSocket': {
                                    'port': 8080
                                }
                            },
                            'name': 'testapp',
                            'image': 'finntech/testimage:version',
                            'volumeMounts': expected_volume_mounts,
                            'env': create_environment_variables(infra, envs, global_env=global_env),
                            'imagePullPolicy': 'IfNotPresent',
                            'readinessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'httpGet': {
                                    'path': '/',
                                    'scheme': 'HTTP',
                                    'port': 8080,
                                    'httpHeaders': []
                                }
                            },
                            'ports': [{'protocol': 'TCP', 'containerPort': 8080, 'name': 'http'}],
                            'resources': {}
                        }]
                    },
                    'metadata': pytest.helpers.create_metadata('testapp', prometheus=True, labels=LABELS)
                },
                'replicas': 3,
                'revisionHistoryLimit': 5
            }
        }
        pytest.helpers.assert_any_call(post, DEPLOYMENTS_URI, expected_deployment)

    def test_deploy_new_admin_deployment(self, infra, global_env, post, deployer, app_spec_with_admin_access):
        deployer.deploy(app_spec_with_admin_access, SELECTOR, LABELS)

        expected_deployment = {
            'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS),
            'spec': {
                'selector': {'matchLabels': SELECTOR},
                'template': {
                    'spec': {
                        'dnsPolicy': 'ClusterFirst',
                        'serviceAccountName': 'default',
                        'restartPolicy': 'Always',
                        'volumes': [],
                        'imagePullSecrets': [],
                        'containers': [{
                            'livenessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'tcpSocket': {
                                    'port': 8080
                                }
                            },
                            'name': 'testapp',
                            'image': 'finntech/testimage:version',
                            'volumeMounts': [],
                            'env': create_environment_variables(infra, global_env=global_env),
                            'imagePullPolicy': 'IfNotPresent',
                            'readinessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'httpGet': {
                                    'path': '/',
                                    'scheme': 'HTTP',
                                    'port': 8080,
                                    'httpHeaders': []
                                }
                            },
                            'ports': [{'protocol': 'TCP', 'containerPort': 8080, 'name': 'http'}],
                            'resources': {}
                        }]
                    },
                    'metadata': pytest.helpers.create_metadata('testapp', prometheus=True, labels=LABELS)
                },
                'replicas': 3,
                'revisionHistoryLimit': 5
            }
        }
        pytest.helpers.assert_any_call(post, DEPLOYMENTS_URI, expected_deployment)

    @pytest.mark.parametrize("enabled,port", (
            (False, None),
            (True, 8080),
            (True, "8080"),
            (True, "http")
    ))
    def test_deployment_prometheus(self, enabled, port, infra, global_env, post, deployer, app_spec):
        app_spec = app_spec._replace(prometheus=PrometheusSpec(enabled, port, '/internal-backstage/prometheus'))
        deployer.deploy(app_spec, SELECTOR, LABELS)

        expected_deployment = {
            'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS),
            'spec': {
                'selector': {'matchLabels': SELECTOR},
                'template': {
                    'spec': {
                        'dnsPolicy': 'ClusterFirst',
                        'serviceAccountName': 'fiaas-no-access',
                        'restartPolicy': 'Always',
                        'volumes': [],
                        'imagePullSecrets': [],
                        'containers': [{
                            'livenessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'tcpSocket': {
                                    'port': 8080
                                }
                            },
                            'name': 'testapp',
                            'image': 'finntech/testimage:version',
                            'volumeMounts': [],
                            'env': create_environment_variables(infra, global_env=global_env),
                            'imagePullPolicy': 'IfNotPresent',
                            'readinessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'httpGet': {
                                    'path': '/',
                                    'scheme': 'HTTP',
                                    'port': 8080,
                                    'httpHeaders': []
                                }
                            },
                            'ports': [{'protocol': 'TCP', 'containerPort': 8080, 'name': 'http'}],
                            'resources': {}
                        }]
                    },
                    'metadata': pytest.helpers.create_metadata('testapp', prometheus=enabled, labels=LABELS)
                },
                'replicas': 3,
                'revisionHistoryLimit': 5
            }
        }
        pytest.helpers.assert_any_call(post, DEPLOYMENTS_URI, expected_deployment)

    @pytest.mark.parametrize("previous_replicas,max_replicas,min_replicas,cpu_request,expected_replicas", (
            (5, 3, 2, None, 3),
            (5, 3, 2, "1", 5),
    ))
    def test_replicas_when_autoscaler_enabled(self, previous_replicas, max_replicas, min_replicas, cpu_request,
                                              expected_replicas, infra, global_env, deployer, app_spec, get, put, post):
        app_spec = app_spec._replace(
            replicas=max_replicas,
            autoscaler=AutoscalerSpec(enabled=True, min_replicas=min_replicas, cpu_threshold_percentage=50),
            image="finntech/testimage:version2")
        if cpu_request:
            app_spec = app_spec._replace(
                resources=ResourcesSpec(
                    requests=ResourceRequirementSpec(cpu=cpu_request, memory=None),
                    limits=ResourceRequirementSpec(cpu=None, memory=None)))
        mock_response = create_autospec(Response)
        mock_response.json.return_value = {
            'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS),
            'spec': {
                'selector': {'matchLabels': SELECTOR},
                'template': {
                    'spec': {
                        'dnsPolicy': 'ClusterFirst',
                        'serviceAccountName': 'fiaas-no-access',
                        'restartPolicy': 'Always',
                        'volumes': [],
                        'imagePullSecrets': [],
                        'containers': [{
                            'livenessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'tcpSocket': {
                                    'port': 8080
                                }
                            },
                            'name': 'testapp',
                            'image': 'finntech/testimage:version',
                            'volumeMounts': [],
                            'env': create_environment_variables(infra, global_env=global_env),
                            'imagePullPolicy': 'IfNotPresent',
                            'readinessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'httpGet': {
                                    'path': '/',
                                    'scheme': 'HTTP',
                                    'port': 8080,
                                    'httpHeaders': []
                                }
                            },
                            'ports': [{'protocol': 'TCP', 'containerPort': 8080, 'name': 'http'}],
                            'resources': {}
                        }]
                    },
                    'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS)
                },
                'replicas': previous_replicas,
                'revisionHistoryLimit': 5
            }
        }
        get.side_effect = None
        get.return_value = mock_response

        deployer.deploy(app_spec, SELECTOR, LABELS)
        expected_deployment = {
            'metadata': pytest.helpers.create_metadata('testapp', labels=LABELS),
            'spec': {
                'selector': {'matchLabels': SELECTOR},
                'template': {
                    'spec': {
                        'dnsPolicy': 'ClusterFirst',
                        'serviceAccountName': 'fiaas-no-access',
                        'restartPolicy': 'Always',
                        'volumes': [],
                        'imagePullSecrets': [],
                        'containers': [{
                            'livenessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'tcpSocket': {
                                    'port': 8080
                                }
                            },
                            'name': 'testapp',
                            'image': 'finntech/testimage:version2',
                            'volumeMounts': [],
                            'env': create_environment_variables(infra, global_env=global_env, version="version2"),
                            'imagePullPolicy': 'IfNotPresent',
                            'readinessProbe': {
                                'initialDelaySeconds': 10,
                                'periodSeconds': 10,
                                'successThreshold': 1,
                                'timeoutSeconds': 1,
                                'httpGet': {
                                    'path': '/',
                                    'scheme': 'HTTP',
                                    'port': 8080,
                                    'httpHeaders': []
                                }
                            },
                            'ports': [{'protocol': 'TCP', 'containerPort': 8080, 'name': 'http'}],
                            'resources': {
                                'requests': {'cpu': cpu_request}
                            } if cpu_request else {}
                        }]
                    },
                    'metadata': pytest.helpers.create_metadata('testapp', prometheus=True, labels=LABELS)
                },
                'replicas': expected_replicas,
                'revisionHistoryLimit': 5
            }
        }
        pytest.helpers.assert_no_calls(post)
        pytest.helpers.assert_any_call(put, DEPLOYMENTS_URI + "testapp", expected_deployment)


def create_environment_variables(infrastructure, envs=False, global_env=None, version="version"):
    environment = [{'name': 'ARTIFACT_NAME', 'value': 'testapp'},
                   {'name': 'LOG_STDOUT', 'value': 'true'},
                   {'name': 'VERSION', 'value': version},
                   {'name': 'CONSTRETTO_TAGS', 'value': 'kubernetes-test,kubernetes,test'},
                   {'name': 'FIAAS_INFRASTRUCTURE', 'value': infrastructure},
                   {'name': 'FIAAS_ENVIRONMENT', 'value': 'test'},
                   {'name': 'LOG_FORMAT', 'value': 'json'},
                   {'name': 'IMAGE', 'value': 'finntech/testimage:' + version},
                   {'name': 'FINN_ENV', 'value': 'test'}, ]
    if global_env:
        environment.append({'name': 'A_GLOBAL_STRING', 'value': global_env['A_GLOBAL_STRING']})
        environment.append({'name': 'FIAAS_A_GLOBAL_STRING', 'value': global_env['A_GLOBAL_STRING']})
        environment.append({'name': 'A_GLOBAL_DIGIT', 'value': global_env['A_GLOBAL_DIGIT']})
        environment.append({'name': 'FIAAS_A_GLOBAL_DIGIT', 'value': global_env['A_GLOBAL_DIGIT']})
    if envs:
        environment.append({'name': 'ENV', 'valueFrom': {
            'configMapKeyRef': {
                'name': 'testapp',
                'key': "ENV"
            }
        }})
    return environment
