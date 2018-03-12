from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

CHANNEL_LAYER = get_channel_layer()


def get_group_user_key(pk):
    """
    Returns the group name for the given user pk which contains his channels.
    """
    key = '.user{pk}'
    return key.format(pk=pk)


def close_user_channels(pk):
    """
    Closes channels for the given user pk.
    """
    async_to_sync(CHANNEL_LAYER.group_send)(
        get_group_user_key(pk),
        {'type': 'websocket.disconnect', 'code': 3},
    )
