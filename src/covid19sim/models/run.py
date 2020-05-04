import os
import json
import functools
from joblib import Parallel, delayed

from covid19sim.server_utils import InferenceClient, InferenceWorker
from covid19sim.configs import config
from ctt.inference.infer import InferenceEngine


def query_inference_server(params, **inf_client_kwargs):
    # Make a request to the server
    client = InferenceClient(**inf_client_kwargs)
    results = client.infer(params)
    return results


def integrated_risk_pred(humans, start, current_day, time_slot, all_possible_symptoms, port=6688, n_jobs=1, data_path=None):
    """ Setup and make the calls to the server"""
    hd = humans[0].city.hd
    exp_config = humans[0].env.exp_config
    all_params = []

    # We're going to send a request to the server for each human
    for human in humans:
        if time_slot not in human.time_slots:
            continue
        log_path = None
        if data_path:
            log_path = f'{os.path.dirname(data_path)}/daily_outputs/{current_day}/{human.name[6:]}/'
        all_params.append({
            "start": start,
            "current_day": current_day,
            "all_possible_symptoms": all_possible_symptoms,
            "human": human.__getstate__(),
            "COLLECT_TRAINING_DATA": exp_config['COLLECT_TRAINING_DATA'],
            "log_path": log_path,
            "risk_model": exp_config['RISK_MODEL'],
        })

    if config.USE_INFERENCE_SERVER:
        batch_start_offset = 0
        batch_size = 25  # @@@@ TODO: make this a high-level configurable arg?
        batched_params = []
        while batch_start_offset < len(all_params):
            batch_end_offset = min(batch_start_offset + batch_size, len(all_params))
            batched_params.append(all_params[batch_start_offset:batch_end_offset])
            batch_start_offset += batch_size
        query_func = functools.partial(query_inference_server, target_port=port)
        with Parallel(n_jobs=n_jobs, batch_size=exp_config['MP_BATCHSIZE'], backend=exp_config['MP_BACKEND'], verbose=0, prefer="threads") as parallel:
            batched_results = parallel((delayed(query_func)(params) for params in batched_params))
        results = []
        for b in batched_results:
            results.extend(b)
    else:
        # recreating an engine every time should not be too expensive... right?
        engine = InferenceEngine(exp_config['TRANSFORMER_EXP_PATH'])
        results = InferenceWorker.process_sample(all_params, engine, exp_config['MP_BACKEND'], n_jobs)

    if exp_config['RISK_MODEL'] != "transformer":
        return humans

    for result in results:
        if result is not None:
            name, risk_history, clusters = result

            for i in range(config.TRACING_N_DAYS_HISTORY):
                hd[name].risk_history_map[current_day - i] = risk_history[i]

            hd[name].update_risk_level()

            for i in range(config.TRACING_N_DAYS_HISTORY):
                hd[name].prev_risk_history_map[current_day - i] = risk_history[i]

            hd[name].clusters = clusters
            hd[name].last_risk_update = current_day
            hd[name].contact_book.update_messages = []
            hd[name].contact_book.messages = []

    # print out the clusters
    if config.DUMP_CLUSTERS:
        clusters = []
        for human in hd.values():
            clusters.append(dict(human.clusters.clusters))
        json.dump(clusters, open(cconfig.onfig.CLUSTER_PATH, 'w'))
    return humans