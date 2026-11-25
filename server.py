#!/usr/bin/env python3
"""
Game server for GoCommuter multiplayer networking
Uses aiohttp + python-socketio for WebSocket-based communication
"""
import pickle
import base64
from typing import Dict
from aiohttp import web
import socketio
from operations import Operations
from game import Game

# Create Socket.IO server
sio = socketio.AsyncServer(cors_allowed_origins="*", async_mode='aiohttp')
app = web.Application()
sio.attach(app)

# Game session management
game_sessions: Dict[str, Dict] = {}  # session_id -> {game, ui: Operations, players: {sid: player_id}, ...}

# Game settings (shared across all clients)
game_settings = {
    'map_width': 100,
    'map_height': 75,
    'player_count': 2,
    'player_names': ['Player1', 'Player2'],
    'starting_money': 50000,
    'worker_replenishment_rate': 1.0
}


def serialize_game_state(game: Game) -> dict:
    """Game state message: the pickled Game (base64) plus a few summary fields."""
    game_data = pickle.dumps(game, protocol=4)
    return {
        'game_data': base64.b64encode(game_data).decode('utf-8'),
        'year': game.get_year(),
        'is_playing': game.is_playing,
        'num_players': len(game.get_players()),
        'active_players': len(game.get_active_players()),
    }


def _operations_init_parameters() -> frozenset:
    import inspect
    params = inspect.signature(Operations.__init__).parameters
    return frozenset(name for name in params if name not in ('self', 'player_names'))


def build_operations(player_names: list, operations_kwargs: dict) -> Operations:
    """Operations for a new networked game from a client's setup settings (unknown keys ignored)."""
    allowed = _operations_init_parameters()
    kwargs = {k: v for k, v in (operations_kwargs or {}).items() if k in allowed}
    # JSON turns int dict keys into strings
    for key in ('ai_focus_per_player', 'ai_difficulty_per_player', 'ai_specialty_per_player'):
        if isinstance(kwargs.get(key), dict):
            kwargs[key] = {int(k): v for k, v in kwargs[key].items()}
    if 'inflation_rate' in kwargs:
        from inflation_utils import set_inflation_rate_multiplier
        set_inflation_rate_multiplier(kwargs['inflation_rate'])
    return Operations(list(player_names), **kwargs)


def human_seats(game: Game) -> list[int]:
    """Player indices that connected clients play (everyone who is not an AI)."""
    return [i for i, p in enumerate(game.get_players()) if not getattr(p, 'is_ai', False)]


async def start_session(creator_sid: str, player_names: list, operations_kwargs: dict, client_sids: list) -> str:
    """Create a game, seat ``client_sids`` in the human seats and send each its game_created."""
    ui = build_operations(player_names, operations_kwargs)
    game = ui.get_game()
    seats = human_seats(game)
    players = dict(zip(client_sids, seats))
    import uuid
    session_id = str(uuid.uuid4())
    game_sessions[session_id] = {
        'game': game,
        'ui': ui,
        'players': players,
        'player_names': [p.get_name() for p in game.get_players()],
        'end_year_ready': set(),
        'creator_sid': creator_sid,
    }
    state = serialize_game_state(game)
    names = game_sessions[session_id]['player_names']
    all_players = [{'player_id': pid, 'player_name': names[pid], 'connected': True} for pid in players.values()]
    for client_sid, player_id in players.items():
        await sio.enter_room(client_sid, session_id)
        await sio.emit('game_created', {
            'session_id': session_id,
            'player_id': player_id,
            'player_name': names[player_id],
            'game_state': state,
            'all_players': all_players,
        }, room=client_sid)
    print(f"Game {session_id} started: {len(players)} connected player(s), "
          f"{len(names) - len(seats)} AI, map {operations_kwargs.get('map_preset_name') or 'default'}")
    return session_id


# Track all connected clients globally
connected_clients: Dict[str, bool] = {}  # sid -> True, in connection order

@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    print(f"Client connected: {sid}")
    connected_clients[sid] = True
    await sio.emit('connected', {'message': 'Connected to GoCommuter server'}, room=sid)
    # Send current game settings to new client
    await sio.emit('settings_updated', game_settings, room=sid)


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    print(f"Client disconnected: {sid}")
    connected_clients.pop(sid, None)
    # Remove player from any game sessions
    for session_id, session in list(game_sessions.items()):
        if sid in session['players']:
            player_id = session['players'][sid]
            player_name = session['game'].get_players()[player_id].get_name()
            del session['players'][sid]
            await sio.emit('player_left', {
                'player_id': player_id,
                'player_name': player_name
            }, room=session_id)
            
            # If no players left, clean up session
            if not session['players']:
                del game_sessions[session_id]
            break


async def _start_for_connected_clients(sid, player_names: list, operations_kwargs: dict):
    if len(player_names) < 2:
        await sio.emit('error', {'message': 'Need at least 2 players'}, room=sid)
        return
    ai_indices = set(operations_kwargs.get('ai_player_indices') or [])
    seat_count = sum(1 for i in range(len(player_names)) if i not in ai_indices)
    seated = {c for session in game_sessions.values() for c in session['players']}
    clients = [c for c in connected_clients if c not in seated]
    if not clients:
        await sio.emit('error', {'message': 'No players connected'}, room=sid)
        return
    if len(clients) > seat_count:
        await sio.emit('error', {
            'message': f'{len(clients)} players are connected but the game has only {seat_count} human seat(s). '
                       'Add players or reduce the number of AI players.',
        }, room=sid)
        return
    # The client starting the game takes the first seat.
    clients.sort(key=lambda c: c != sid)
    await start_session(sid, player_names, operations_kwargs, clients)


@sio.event
async def join_game(sid, data):
    """Join an existing game session"""
    try:
        session_id = data.get('session_id')
        
        if session_id not in game_sessions:
            await sio.emit('error', {'message': 'Game session not found'}, room=sid)
            return
        
        session = game_sessions[session_id]
        game = session['game']
        player_names = session['player_names']
        
        connected_player_ids = set(session['players'].values())
        available_slots = [i for i in human_seats(game) if i not in connected_player_ids]
        
        if not available_slots:
            await sio.emit('error', {'message': 'Game is full'}, room=sid)
            return
        
        player_id = available_slots[0]
        session['players'][sid] = player_id
        
        await sio.enter_room(sid, session_id)
        
        # Notify all players
        await sio.emit('player_joined', {
            'player_id': player_id,
            'player_name': player_names[player_id],
            'total_players': len(session['players'])
        }, room=session_id)
        
        # Send game state to new player
        await sio.emit('game_state', {
            'game_state': serialize_game_state(game),
            'player_id': player_id,
            'player_name': player_names[player_id]
        }, room=sid)
        
        print(f"Player {player_id} ({player_names[player_id]}) joined game {session_id}")
        
    except Exception as e:
        print(f"Error joining game: {e}")
        await sio.emit('error', {'message': str(e)}, room=sid)


@sio.event
async def get_game_state(sid, data):
    """Request current game state"""
    try:
        session_id = data.get('session_id')
        
        if session_id not in game_sessions:
            await sio.emit('error', {'message': 'Game session not found'}, room=sid)
            return
        
        session = game_sessions[session_id]
        game = session['game']
        
        await sio.emit('game_state', {
            'game_state': serialize_game_state(game)
        }, room=sid)
        
    except Exception as e:
        print(f"Error getting game state: {e}")
        await sio.emit('error', {'message': str(e)}, room=sid)


@sio.event
async def player_action(sid, data):
    """A player's action: an Operations call (operations_call) or being ready to end the year (end_year)."""
    try:
        session_id = data.get('session_id')
        action_type = data.get('action_type')
        action_data = data.get('action_data', {})
        
        if session_id not in game_sessions:
            await sio.emit('error', {'message': 'Game session not found'}, room=sid)
            return
        
        session = game_sessions[session_id]
        
        if sid not in session['players']:
            await sio.emit('error', {'message': 'Not part of this game session'}, room=sid)
            return
        
        player_id = session['players'][sid]
        game = session['game']
        ui = session['ui']
        player = game.get_players()[player_id]
        
        # All players act simultaneously (no turns).
        if action_type == 'operations_call':
            # action_data: network_rpc.encode_operations_call(...) - any game-changing Operations call
            result = handle_operations_call(ui, player, action_data)

        elif action_type == 'end_year':
            # The year ends once every connected, living human is ready (the server runs the AIs).
            players = game.get_players()
            active_player_ids = {
                pid for pid in session['players'].values() if not players[pid].is_eliminated
            }
            session['end_year_ready'].add(player_id)
            
            # Check if all active players are ready
            if active_player_ids.issubset(session['end_year_ready']):
                # All players ready - advance the year
                result = await handle_advance_year(ui, game)
                if result['success']:
                    # Clear the ready set for the next year
                    session['end_year_ready'] = set()
                    # Broadcast that year has advanced with full game state
                    await sio.emit('year_advanced', {
                        'new_year': game.get_year(),
                        'message': result.get('message', 'Year advanced'),
                        'game_state': serialize_game_state(game)  # Send full game state so clients can update year display
                    }, room=session_id)
                else:
                    # Year advance failed - remove this player from ready
                    session['end_year_ready'].discard(player_id)
                    result['message'] = 'Year advance failed: ' + result.get('message', 'Unknown error')
            else:
                # Not all players ready yet
                ready_count = len(session['end_year_ready'])
                total_count = len(active_player_ids)
                result = {
                    'success': True,
                    'message': f'Ready to end year ({ready_count}/{total_count} players ready)',
                    'waiting_for_others': True,
                    'ready_count': ready_count,
                    'total_count': total_count
                }
                # Broadcast update to all players about who's ready
                await sio.emit('end_year_status', {
                    'ready_count': ready_count,
                    'total_count': total_count,
                    'ready_players': [session['player_names'][pid] for pid in session['end_year_ready']]
                }, room=session_id)
        
        else:
            result = {'success': False, 'message': f'Unknown action type: {action_type}'}
        
        # Everyone gets the new state after every action (even a failed one: the state is still
        # the latest); the acting client is also told about a failure.
        await sio.emit('game_state', {
            'game_state': serialize_game_state(game),
            'action_result': result,
        }, room=session_id)
        if not result.get('success'):
            await sio.emit('action_failed', result, room=sid)

    except Exception as e:
        print(f"Error processing action: {e}")
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'message': str(e)}, room=sid)


# Operations calls that address the shared build queue by index (first args).
BUILD_QUEUE_METHODS = frozenset({'cancel_build', 'assign_workers_to_build', 'reorder_build_queue'})


def handle_operations_call(ui, player, action_data):
    """Run a client's Operations call on the server's game, as that client's player."""
    from network_rpc import decode_operations_call, lines_in_call, players_in_call
    try:
        method, args, kwargs = decode_operations_call(ui.get_game(), action_data)
    except Exception as e:
        return {'success': False, 'message': f'Rejected action: {e}'}
    # A player may only act for themselves (the first player argument is the one acting)
    # and only change their own lines when no player is named.
    acting = players_in_call(args, kwargs)
    if acting and acting[0] is not player:
        return {'success': False, 'message': f'{method}: you can only act for your own company'}
    if not acting:
        for line in lines_in_call(args, kwargs):
            if getattr(line, 'owner', player) is not player:
                return {'success': False, 'message': f'{method}: {line.name} is not your line'}
    if method in BUILD_QUEUE_METHODS:
        queue = ui.get_game().build_queue
        index = args[0] if args else None
        if not isinstance(index, int) or not (0 <= index < len(queue)) or queue[index].get('player') is not player:
            return {'success': False, 'message': f'{method}: not one of your constructions'}
    try:
        outcome = getattr(ui, method)(*args, **kwargs)
    except (ValueError, KeyError, IndexError) as e:
        return {'success': False, 'message': str(e)}
    if isinstance(outcome, dict) and outcome.get('success') is False:
        return {'success': False, 'message': outcome.get('error_message') or outcome.get('message') or f'{method} failed'}
    return {'success': True, 'method': method}


async def handle_advance_year(ui, game):
    """Handle advance year action: run AI turns, then advance_year_complete. Newly spawned AIs also get a turn."""
    try:
        old_year = game.get_year()
        from ai import (
            run_ai_turn,
            AIIdleTurnError,
            validate_ai_turn_results,
            run_pending_ai_in_year_turns,
            AI_TURN_TIME_BUDGET_SEC,
        )
        for p in ui.get_active_players():
            if not getattr(p, 'is_ai', False):
                ui.mark_human_ready_for_year_end(p)
        pending = ui.get_ai_players_needing_in_year_turn()
        run_pending_ai_in_year_turns(
            ui, players=pending, deadline_sec=AI_TURN_TIME_BUDGET_SEC
        )
        if not ui.all_players_completed_year_actions():
            return {
                'success': False,
                'message': 'Cannot advance year until every player has finished (waiting on AI turns).',
            }
        # Use the proper advance_year_complete method which handles all year-end logic
        result = ui.advance_year_complete(old_year)
        # AIs that spawn after turn 1 get to take actions in the same round
        if result and result.get('new_ai_spawned'):
            new_info = result['new_ai_spawned']
            new_player = new_info.get('player') if isinstance(new_info, dict) else None
            if new_player is not None:
                try:
                    acts = run_ai_turn(ui, new_player)
                    validate_ai_turn_results(ui, new_player, acts)
                    ui.mark_ai_in_year_turn_completed(new_player)
                except AIIdleTurnError as e:
                    print(f"AI turn idle for newly spawned {new_player.get_name()}: {e}")
                    ui.mark_ai_in_year_turn_completed(new_player)
                except Exception as e:
                    print(f"AI turn failed for newly spawned {new_player.get_name()}: {e}")
                    ui.mark_ai_in_year_turn_completed(new_player)
                    import traceback
                    traceback.print_exc()
        new_year = result.get('new_year', old_year + 1)
        return {
            'success': True, 
            'message': f'Year advanced to {new_year}',
            'new_year': new_year
        }
    except Exception as e:
        print(f"Error advancing year: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


@sio.event
async def list_games(sid, data):
    """List available game sessions"""
    available_games = []
    for session_id, session in game_sessions.items():
        game = session['game']
        available_games.append({
            'session_id': session_id,
            'num_players': len(session['players']),
            'max_players': len(game.get_players()),
            'year': game.get_year(),
            'is_playing': game.is_playing
        })
    
    await sio.emit('games_list', {'games': available_games}, room=sid)


@sio.event
async def chat(sid, data):
    """Handle chat messages. Server controller (game creator) can use /commands."""
    try:
        session_id = data.get('session_id')
        message = (data.get('message') or '').strip()
        if not message:
            return
        if session_id not in game_sessions:
            await sio.emit('error', {'message': 'Not in a game session'}, room=sid)
            return
        session = game_sessions[session_id]
        game = session['game']
        player_names = session['player_names']
        player_id = session['players'].get(sid)
        sender_name = player_names[player_id] if player_id is not None and 0 <= player_id < len(player_names) else 'Unknown'
        creator_sid = session.get('creator_sid')
        # Commands: only game creator can use
        if message.startswith('/') and sid == creator_sid:
            parts = message[1:].split(maxsplit=2)
            cmd = (parts[0] or '').lower()
            if cmd == 'save':
                import os
                save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'savegames')
                os.makedirs(save_dir, exist_ok=True)
                import time
                save_name = f"server_save_{int(time.time())}"
                save_path = os.path.join(save_dir, f"{save_name}.pkl")
                try:
                    save_data = {'game': game, 'current_player_index': 0}
                    with open(save_path, 'wb') as f:
                        pickle.dump(save_data, f)
                    msg = f"Server: Game saved as {save_name}"
                    await sio.emit('chat_message', {'message': msg, 'sender': 'Server'}, room=session_id)
                except Exception as e:
                    await sio.emit('chat_message', {'message': f"Server: Save failed: {e}", 'sender': 'Server'}, room=sid)
            elif cmd == 'chat':
                # /chat [player] [message] or /chat [message]
                if len(parts) >= 2:
                    arg = parts[1]
                    if len(parts) >= 3:
                        target_name, rest = arg, parts[2]
                        target_id = next((i for i, n in enumerate(player_names) if n.lower() == target_name.lower()), None)
                        if target_id is not None:
                            target_sid = next((s for s, pid in session['players'].items() if pid == target_id), None)
                            if target_sid:
                                await sio.emit('chat_message', {'message': f"Server: {rest}", 'sender': 'Server'}, room=target_sid)
                            else:
                                await sio.emit('chat_message', {'message': f"Server: Player {target_name} not connected", 'sender': 'Server'}, room=sid)
                        else:
                            await sio.emit('chat_message', {'message': f"Server: Unknown player {target_name}", 'sender': 'Server'}, room=sid)
                    else:
                        await sio.emit('chat_message', {'message': f"Server: {arg}", 'sender': 'Server'}, room=session_id)
                else:
                    await sio.emit('error', {'message': 'Usage: /chat [player] [message] or /chat [message]'}, room=sid)
            else:
                await sio.emit('chat_message', {'message': f"Server: Unknown command. Use /save or /chat [player] [message]", 'sender': 'Server'}, room=sid)
        else:
            if message.startswith('/') and sid != creator_sid:
                await sio.emit('chat_message', {'message': 'Only the game creator can use /commands', 'sender': 'Server'}, room=sid)
            else:
                await sio.emit('chat_message', {'message': message, 'sender': sender_name}, room=session_id)
    except Exception as e:
        print(f"Error in chat: {e}")
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'message': str(e)}, room=sid)


@sio.event
async def update_setting(sid, data):
    """Handle setting change from client and broadcast to all"""
    setting_name = data.get('setting')
    value = data.get('value')
    
    if setting_name in game_settings:
        game_settings[setting_name] = value
        # Broadcast to all connected clients
        await sio.emit('settings_updated', {setting_name: value}, room=None)
        print(f"Setting {setting_name} updated to {value} by {sid}")


@sio.event
async def start_game(sid, data):
    """Start a game for every connected client with the starting client's settings."""
    try:
        data = data or {}
        player_names = data.get('player_names') or game_settings.get('player_names', ['Player1', 'Player2'])
        from map_preset_loader import get_new_game_default_preset
        operations_kwargs = data.get('operations_kwargs') or {
            'map_preset_name': get_new_game_default_preset(),
            'map_width': game_settings.get('map_width', 100),
            'map_height': game_settings.get('map_height', 75),
            'starting_money': game_settings.get('starting_money', 50000),
            'worker_replenishment_rate': game_settings.get('worker_replenishment_rate', 1.0),
        }
        if 'ai_player_indices' in data:
            operations_kwargs = {**operations_kwargs, 'ai_player_indices': data.get('ai_player_indices') or []}
        await _start_for_connected_clients(sid, list(player_names), operations_kwargs)
    except Exception as e:
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'message': str(e)}, room=sid)


async def index(request: web.Request):
    """Health check endpoint"""
    return web.json_response({
        'status': 'running',
        'active_sessions': len(game_sessions)
    })

app.router.add_get('/', index)


if __name__ == '__main__':
    import sys
    
    # Default port
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    
    print(f"Starting GoCommuter server on port {port}")
    print("Server will accept connections from clients")
    print(f"Server listening on all interfaces (0.0.0.0:{port})")
    print(f"Connect from other devices using: http://<server_ip>:{port}")
    
    web.run_app(app, host='0.0.0.0', port=port)

