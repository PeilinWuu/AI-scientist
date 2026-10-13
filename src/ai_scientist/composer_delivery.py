"""Retry uploaded attachments without duplicating already acknowledged files."""
import hashlib
import json


def save_attachments_once(uploads, options, description, receipts, save_one):
    if len(uploads) > 20:
        return [], ['每次最多添加 20 个附件；尚未保存或发送。']
    saved, errors = [], []
    for upload in uploads:
        key = hashlib.sha256(upload.getvalue() + json.dumps(
            [upload.name, options, description[:2000]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key not in receipts:
            items, failures = save_one(upload)
            errors.extend(failures)
            if items:
                receipts[key] = items[0]
        if key in receipts:
            item = receipts[key]
            if item not in saved:
                saved.append(item)
    return saved, errors
