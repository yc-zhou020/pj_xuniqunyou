"""Explicit private/group targets; group IDs never masquerade as user sec_uids."""


def is_group(target):
    return target.get('kind') == 'group'


def target_key(target):
    return 'group:' + target['group_id'] if is_group(target) else target['sec_uid']


def active_targets(settings):
    return [target for target in settings.get('whitelist', []) + settings.get('group_whitelist', [])
            if target.get('enabled', True) and not target.get('is_self', False)]


def allowed_snapshot(snapshot, settings):
    if snapshot.friend not in active_targets(settings):
        return False
    if is_group(snapshot.friend):
        allowed = {member['user_id'] for member in snapshot.friend['members'] if not member.get('is_self')}
        return all(snapshot.messages[i].sender_id in allowed for i in snapshot.selected)
    return True
