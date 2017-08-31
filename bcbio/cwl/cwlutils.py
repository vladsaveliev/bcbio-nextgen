"""Useful utilities for handling CWL inputs and outputs.

This is shared functionality abstracted across multiple approaches, currently
mostly handling CWL records. This needs some generalization to apply across
non-variant calling workflows.
"""
import collections
import os
import pprint
import tarfile

import toolz as tz

from bcbio import utils
from bcbio.pipeline import datadict as dd

def to_rec(samples, default_keys=None):
    """Convert inputs into CWL records, useful for single item parallelization.
    """
    recs = samples_to_records([utils.to_single_data(x) for x in samples], default_keys)
    return [[x] for x in recs]

def to_rec_single(samples, default_keys=None):
    """Convert output into a list of single CWL records.
    """
    out = []
    for data in samples:
        recs = samples_to_records([utils.to_single_data(data)], default_keys)
        assert len(recs) == 1
        out.append(recs[0])
    return out

def normalize_missing(xs):
    """Normalize missing values to avoid string 'None' inputs.
    """
    if isinstance(xs, dict):
        for k, v in xs.items():
            xs[k] = normalize_missing(v)
    elif isinstance(xs, (list, tuple)):
        xs = [normalize_missing(x) for x in xs]
    elif isinstance(xs, str):
        if xs.lower() in ["none", "null"]:
            xs = None
        elif xs.lower() == "true":
            xs = True
        elif xs.lower() == "false":
            xs = False
    return xs

# aligner and database indices where we list the entire directory as secondary files
DIR_TARGETS = ("mainIndex", ".alt", ".amb", ".ann", ".bwt", ".pac", ".sa", ".ebwt", ".bt2",
               "Genome", "GenomeIndex", "GenomeIndexHash", "OverflowTable", ".fa")

def unpack_tarballs(xs, data, use_subdir=True):
    """Unpack workflow tarballs into ready to use directories.
    """
    if isinstance(xs, dict):
        for k, v in xs.items():
            xs[k] = unpack_tarballs(v, data, use_subdir)
    elif isinstance(xs, (list, tuple)):
        xs = [unpack_tarballs(x, data, use_subdir) for x in xs]
    elif isinstance(xs, str):
        if os.path.isfile(xs) and xs.endswith("-wf.tar.gz"):
            if use_subdir:
                tarball_dir = utils.safe_makedir(os.path.join(dd.get_work_dir(data), "wf-inputs"))
            else:
                tarball_dir = dd.get_work_dir(data)
            out_dir = os.path.join(tarball_dir,
                                   os.path.basename(xs).replace("-wf.tar.gz", "").replace("--", os.path.sep))
            if not os.path.exists(out_dir):
                with utils.chdir(tarball_dir):
                    with tarfile.open(xs, "r:gz") as tar:
                        tar.extractall()
            assert os.path.exists(out_dir), out_dir
            # Default to representing output directory
            xs = out_dir
            # Look for aligner indices
            for fname in os.listdir(out_dir):
                if fname.endswith(DIR_TARGETS):
                    xs = os.path.join(out_dir, fname)
                    break
    return xs

def _get_all_cwlkeys(items, default_keys=None):
    """Retrieve cwlkeys from inputs, handling defaults which can be null.

    When inputs are null in some and present in others, this creates unequal
    keys in each sample, confusing decision making about which are primary and extras.
    """
    if default_keys:
        default_keys = set(default_keys)
    else:
        default_keys = set(["metadata__batch", "config__algorithm__validate",
                            "config__algorithm__validate_regions",
                            "config__algorithm__validate_regions_merged",
                            "config__algorithm__variant_regions",
                            "validate__summary",
                            "validate__tp", "validate__fp", "validate__fn",
                            "config__algorithm__coverage", "config__algorithm__coverage_merged",
                            "genome_resources__variation__cosmic", "genome_resources__variation__dbsnp",
        ])
    all_keys = set([])
    for data in items:
        all_keys.update(set(data["cwl_keys"]))
    all_keys.update(default_keys)
    return all_keys

def split_data_cwl_items(items, default_keys=None):
    """Split a set of CWL output dictionaries into data samples and CWL items.

    Handles cases where we're arrayed on multiple things, like a set of regional
    VCF calls and data objects.
    """
    key_lens = set([])
    for data in items:
        key_lens.add(len(_get_all_cwlkeys([data], default_keys)))
    extra_key_len = min(list(key_lens)) if len(key_lens) > 1 else None
    data_out = []
    extra_out = []
    for data in items:
        if extra_key_len and len(_get_all_cwlkeys([data], default_keys)) == extra_key_len:
            extra_out.append(data)
        else:
            data_out.append(data)
    if len(extra_out) == 0:
        return data_out, {}
    else:
        cwl_keys = extra_out[0]["cwl_keys"]
        for extra in extra_out[1:]:
            cur_cwl_keys = extra["cwl_keys"]
            assert cur_cwl_keys == cwl_keys, pprint.pformat(extra_out)
        cwl_extras = collections.defaultdict(list)
        for data in items:
            for key in cwl_keys:
                cwl_extras[key].append(data[key])
        data_final = []
        for data in data_out:
            for key in cwl_keys:
                data.pop(key)
            data_final.append(data)
        return data_final, dict(cwl_extras)

def samples_to_records(samples, default_keys=None):
    """Convert samples into output CWL records.
    """
    from bcbio.pipeline import run_info
    RECORD_CONVERT_TO_LIST = set(["config__algorithm__tools_on", "config__algorithm__tools_off",
                                  "reference__genome_context"])
    all_keys = _get_all_cwlkeys(samples, default_keys)
    out = []
    for data in samples:
        for raw_key in sorted(list(all_keys)):
            key = raw_key.split("__")
            if tz.get_in(key, data) is None:
                data = tz.update_in(data, key, lambda x: None)
            if raw_key not in data["cwl_keys"]:
                data["cwl_keys"].append(raw_key)
            if raw_key in RECORD_CONVERT_TO_LIST:
                val = tz.get_in(key, data)
                if not val: val = []
                elif not isinstance(val, (list, tuple)): val = [val]
                data = tz.update_in(data, key, lambda x: val)
            # Booleans are problematic for CWL serialization, convert into string representation
            if isinstance(tz.get_in(key, data), bool):
                data = tz.update_in(data, key, lambda x: str(tz.get_in(key, data)))
        data["metadata"] = run_info.add_metadata_defaults(data.get("metadata", {}))
        out.append(data)
    return out
