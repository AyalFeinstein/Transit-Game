#!/usr/bin/env python3
"""
Game server for TransitGame multiplayer networking
Uses aiohttp + python-socketio for WebSocket-based communication
"""
import asyncio
import json
import pickle
import base64
from typing import Dict, Optional
from aiohttp import web
import socketio
from operations import setup_game, Operations
from game import Game
from player import Player

# Create Socket.IO server
sio = socketio.AsyncServer(cors_allowed_origins="*", async_mode='aiohttp')
app = web.Application()
sio.attach(app)

# Game session management
game_sessions: Dict[str, Dict] = {}  # session_id -> {game: Game, ui: Operations, players: {sid: player_id}, current_turn: int}

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
    """Serialize game state to JSON-serializable format"""
    try:
        print(f"Serializing game state: game type={type(game)}, year={game.get_year()}, players={len(game.get_players())}")
        
        # For now, use pickle + base64 for complex objects
        # Use protocol 4 for better compatibility (protocol 5 requires Python 3.8+)
        # In production, you'd want a more efficient serialization
        game_data = pickle.dumps(game, protocol=4)
        print(f"Pickled game data: {len(game_data)} bytes")
        
        # Encode to base64 for JSON transmission
        game_data_b64 = base64.b64encode(game_data).decode('utf-8')
        print(f"Base64 encoded: {len(game_data_b64)} characters")
        
        result = {
            'game_data': game_data_b64,
            'year': game.get_year(),
            'is_playing': game.is_playing,
            'num_players': len(game.get_players()),
            'active_players': len(game.get_active_players())
        }
        
        print(f"Serialized state keys: {list(result.keys())}, game_data length: {len(result['game_data'])}")
        return result
    except (pickle.PicklingError, TypeError) as e:
        print(f"Pickling error serializing game state: {e}")
        print(f"Game type: {type(game)}")
        print(f"Game attributes: {dir(game)}")
        import traceback
        traceback.print_exc()
        raise
    except Exception as e:
        print(f"Unexpected error serializing game state: {e}")
        import traceback
        traceback.print_exc()
        raise




# Track all connected clients globally
connected_clients = set()

@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    print(f"Client connected: {sid}")
    connected_clients.add(sid)
    await sio.emit('connected', {'message': 'Connected to TransitGame server'}, room=sid)
    # Send current game settings to new client
    await sio.emit('settings_updated', game_settings, room=sid)


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    print(f"Client disconnected: {sid}")
    connected_clients.discard(sid)
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


@sio.event
async def create_game(sid, data):
    """Create a new game session and automatically assign all connected players"""
    try:
        player_names = data.get('player_names', [])
        map_width = data.get('map_width', 100)
        map_height = data.get('map_height', 75)
        starting_money = data.get('starting_money', 50000)
        worker_replenishment_rate = data.get('worker_replenishment_rate', 1.0)
        
        if len(player_names) < 2:
            await sio.emit('error', {'message': 'Need at least 2 players'}, room=sid)
            return
        
        # Get all currently connected clients
        # Limit to number of player names available
        num_players = len(player_names)
        connected_sessions = list(connected_clients)[:num_players]
        
        if len(connected_sessions) < 2:
            await sio.emit('error', {'message': 'Not enough players connected. Need at least 2 players connected to server.'}, room=sid)
            return
        
        # Create game via Operations (single source of truth)
        ai_player_indices = data.get('ai_player_indices') or []
        ui = Operations(player_names, map_width, map_height, starting_money, worker_replenishment_rate, ai_player_indices=ai_player_indices)
        game = ui.get_game()
        
        import uuid
        session_id = str(uuid.uuid4())
        
        players_dict = {}
        for i, client_sid in enumerate(connected_sessions):
            players_dict[client_sid] = i
        
        game_sessions[session_id] = {
            'game': game,
            'ui': ui,
            'players': players_dict,
            'current_turn': 0,  
            'player_names': player_names,
            'end_year_ready': set(),
            'creator_sid': sid,
        }
        
        for client_sid, player_id in players_dict.items():
            await sio.enter_room(client_sid, session_id)
            
            await sio.emit('game_created', {
                'session_id': session_id,
                'player_id': player_id,
                'player_name': player_names[player_id],
                'game_state': serialize_game_state(game),
                'all_players': [{'player_id': pid, 'player_name': player_names[pid], 'connected': True} 
                               for pid in range(len(connected_sessions))]
            }, room=client_sid)
        
        print(f"Game created: {session_id} by {sid} with {len(connected_sessions)} connected players")
        
    except Exception as e:
        print(f"Error creating game: {e}")
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'message': str(e)}, room=sid)


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
        available_slots = [i for i in range(len(player_names)) if i not in connected_player_ids]
        
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
    """Handle player action (build line, buy trainset, advance year, etc.)"""
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
        
        # No turn-based restrictions - all players act simultaneously
        
        # Process action
        result = {'success': False, 'message': 'Unknown action'}
        
        if action_type == 'build_line':
            # action_data: {'name': str, 'points': list, 'construction_workers': int}
            result = await handle_build_line(ui, player, action_data)
            
        elif action_type == 'buy_trainset':
            # action_data: {'item_id': str or index}
            result = await handle_buy_trainset(ui, player, action_data)
            
        elif action_type == 'assign_trainset':
            # action_data: {'trainset_index': int, 'line_name': str}
            result = await handle_assign_trainset(ui, player, action_data)
            
        elif action_type == 'end_year':
            # Track that this player is ready to end the year
            active_players = game.get_active_players()
            active_player_ids = {i for i, p in enumerate(game.get_players()) if not p.is_eliminated}
            
            # Initialize end_year_ready if it doesn't exist (for backwards compatibility)
            if 'end_year_ready' not in session:
                session['end_year_ready'] = set()
            
            # Add this player to the ready set
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
        
        elif action_type == 'advance_year':
            # Legacy support - redirect to end_year
            result = {'success': False, 'message': 'Use "end_year" action instead'}
            
        elif action_type == 'rent_workers':
            # action_data: {'worker_type_name': str, 'quantity': int}
            result = await handle_rent_workers(ui, player, action_data, game)
            
        elif action_type == 'sell_trainset':
            # action_data: {'trainset_index': int}
            result = await handle_sell_trainset(ui, player, action_data)
            
        elif action_type == 'end_turn':
            # No longer needed in simultaneous play mode
            result = {'success': False, 'message': 'Turn-based mode is disabled - use "end_year" instead'}
            
        else:
            result = {'success': False, 'message': f'Unknown action type: {action_type}'}
        
        # Broadcast updated game state to all players (ALWAYS broadcast to keep marketplace in sync)
        # Even if action failed, we still broadcast to ensure all clients have latest state
        if result.get('success'):
            # Broadcast game state to keep all clients synchronized (especially marketplace stock)
            await sio.emit('game_state', {
                'game_state': serialize_game_state(game),
                'action_result': result
            }, room=session_id)
        else:
            # Still broadcast game state even on failure to keep marketplace in sync
            # (in case stock changed due to another player's action)
            await sio.emit('game_state', {
                'game_state': serialize_game_state(game),
                'action_result': result
            }, room=session_id)
            # Also send failure notification to the requesting client
            await sio.emit('action_failed', result, room=sid)
        
    except Exception as e:
        print(f"Error processing action: {e}")
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'message': str(e)}, room=sid)


async def handle_build_line(ui, player, action_data):
    """Handle build line action"""
    try:
        name = action_data.get('name')
        points = action_data.get('points')
        construction_workers = action_data.get('construction_workers', 0)
        
        if not name or not points:
            return {'success': False, 'message': 'Missing name or points'}
        
        if len(points) < 2:
            return {'success': False, 'message': 'Line must have at least 2 points'}
        
        # Call UI method to build line
        result = ui.create_line_complete(name, points, player)
        return {
            'success': True,
            'message': f"Line '{name}' construction started",
            'cost': result.get('cost', 0),
            'build_time': result.get('build_time', 0)
        }
    except ValueError as e:
        return {'success': False, 'message': str(e)}
    except Exception as e:
        print(f"Error in handle_build_line: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


async def handle_buy_trainset(ui, player, action_data):
    """Handle buy trainset/vehicle action with quantity checking"""
    try:
        # item_id is marketplace index
        item_index = action_data.get('item_id')
        if item_index is None:
            return {'success': False, 'message': 'Missing item_id'}
        
        try:
            item_index = int(item_index)
        except (ValueError, TypeError):
            return {'success': False, 'message': 'Invalid item_id format'}
        
        # Get marketplace items
        marketplace = ui.game.get_marketplace()
        sale_items = marketplace.get_available_items(ui.game.get_year())
        
        if not sale_items or item_index < 0 or item_index >= len(sale_items):
            return {'success': False, 'message': 'Invalid item index'}
        
        sale_item = sale_items[item_index]
        current_year = ui.game.get_year()
        
        # Check quantity BEFORE purchase
        quantity_before = sale_item.get_quantity_available()
        if quantity_before <= 0:
            return {'success': False, 'message': 'Item is out of stock'}
        
        # Determine if this is a line or vehicle
        from line import Line
        vehicle_or_line = sale_item.get_item()
        is_line = isinstance(vehicle_or_line, Line)
        
        # Get item identifier for tracking
        if is_line:
            item_name = vehicle_or_line.name
        else:
            from vehicle import Trainset
            if isinstance(vehicle_or_line, Trainset):
                item_name = vehicle_or_line.locomotive.name if hasattr(vehicle_or_line, 'locomotive') and vehicle_or_line.locomotive else "Unknown"
            else:
                item_name = getattr(vehicle_or_line, 'name', 'Unknown')
        
        # Purchase the item using the appropriate UI method
        try:
            if is_line:
                # Handle line purchase
                result_line = ui.buy_line_by_purchase(sale_item, player)
                if result_line:
                    line_name = sale_item.get_item().name
                    # Check quantity AFTER purchase
                    quantity_after = sale_item.get_quantity_available()
                    result = {
                        'success': True,
                        'message': f'Purchased line {line_name} for ${sale_item.get_price()}',
                        'remaining_money': player.get_money(),
                        'item_name': line_name,
                        'item_type': 'line',
                        'quantity_before': quantity_before,
                        'quantity_after': quantity_after
                    }
                else:
                    result = {'success': False, 'message': 'Failed to purchase line - insufficient funds or other error'}
            else:
                # Handle vehicle purchase
                purchase_result = ui.buy_vehicle_complete(sale_item, player, current_year)
                # Find the SaleItem again to check quantity after purchase
                # It might have been removed or quantity changed
                quantity_after = 0
                target_list = marketplace.available_lines if is_line else marketplace.available_vehicles
                for si in target_list:
                    si_item = si.get_item()
                    if (si_item == vehicle_or_line or 
                        (hasattr(si_item, 'name') and hasattr(vehicle_or_line, 'name') and 
                         si_item.name == vehicle_or_line.name and type(si_item) == type(vehicle_or_line))):
                        quantity_after = si.get_quantity_available()
                        break
                
                result = {
                    'success': True,
                    'message': f'Purchased {purchase_result.get("item_name", "item")} for ${purchase_result.get("price", 0)}',
                    'remaining_money': purchase_result.get('remaining_money', 0),
                    'item_name': purchase_result.get('item_name', item_name),
                    'item_type': 'vehicle',
                    'quantity_before': quantity_before,
                    'quantity_after': quantity_after
                }
            
            # Return the result (either line or vehicle purchase)
            return result
        except ValueError as e:
            return {'success': False, 'message': str(e)}
        except Exception as e:
            print(f"Error purchasing item: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'message': str(e)}
    except Exception as e:
        print(f"Error in handle_buy_trainset: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


async def handle_assign_trainset(ui, player, action_data):
    """Handle assign trainset to line action"""
    try:
        trainset_index = action_data.get('trainset_index')
        line_name = action_data.get('line_name')
        
        if trainset_index is None or not line_name:
            return {'success': False, 'message': 'Missing trainset_index or line_name'}
        
        # Get player's unassigned trainsets
        unassigned = ui.get_unassigned_trainsets(player)
        if not unassigned or trainset_index < 0 or trainset_index >= len(unassigned):
            return {'success': False, 'message': 'Invalid trainset index'}
        
        trainset = unassigned[trainset_index]
        
        # Assign trainset to line
        try:
            ui.assign_trainset_to_line(trainset, line_name)
            return {
                'success': True,
                'message': f'Trainset assigned to line {line_name}'
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
    except Exception as e:
        print(f"Error in handle_assign_trainset: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


async def handle_sell_trainset(ui, player, action_data):
    """Handle sell trainset for half price action"""
    try:
        trainset_index = action_data.get('trainset_index')
        
        if trainset_index is None:
            return {'success': False, 'message': 'Missing trainset_index'}
        
        try:
            trainset_index = int(trainset_index)
        except (ValueError, TypeError):
            return {'success': False, 'message': 'Invalid trainset_index format'}
        
        # Get player's trainsets
        trainsets = ui.get_player_trainsets(player)
        if not trainsets or trainset_index < 0 or trainset_index >= len(trainsets):
            return {'success': False, 'message': 'Invalid trainset index'}
        
        trainset = trainsets[trainset_index]
        
        # Sell trainset using UI method
        try:
            result = ui.sell_trainset_for_half_price(trainset, player)
            return {
                'success': True,
                'message': f'Trainset sold for ${result.get("sale_price", 0)}',
                'sale_price': result.get('sale_price', 0),
                'new_money': result.get('new_money', 0)
            }
        except ValueError as e:
            return {'success': False, 'message': str(e)}
        except Exception as e:
            print(f"Error selling trainset: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'message': str(e)}
    except Exception as e:
        print(f"Error in handle_sell_trainset: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


async def handle_rent_workers(ui, player, action_data, game):
    """Handle rent workers action with quantity checking"""
    try:
        worker_type_name = action_data.get('worker_type_name')
        quantity = action_data.get('quantity')
        
        if worker_type_name is None or quantity is None:
            return {'success': False, 'message': 'Missing worker_type_name or quantity'}
        
        try:
            quantity = int(quantity)
        except (ValueError, TypeError):
            return {'success': False, 'message': 'Invalid quantity format'}
        
        if quantity <= 0:
            return {'success': False, 'message': 'Quantity must be positive'}
        
        marketplace = ui.game.get_marketplace()
        current_year = ui.game.get_year()
        
        # Check worker supply BEFORE rental
        supply_before = marketplace.get_worker_supply(worker_type_name)
        if supply_before < quantity:
            return {'success': False, 'message': f'Not enough {worker_type_name} available. Only {supply_before} available, requested {quantity}'}
        
        # Import worker types
        from worker import ENGINEER, CONSTRUCTION_WORKER, LAWYER
        worker_map = {
            'Engineer': ENGINEER,
            'Construction Worker': CONSTRUCTION_WORKER,
            'Lawyer': LAWYER
        }
        
        worker_type = worker_map.get(worker_type_name)
        if not worker_type:
            return {'success': False, 'message': f'Unknown worker type: {worker_type_name}'}
        
        # Calculate cost
        try:
            cost = ui.calculate_worker_rental_cost(worker_type, quantity, current_year)
        except:
            # Fallback cost calculation
            base_cost = 100 * quantity
            cost = base_cost
        
        # Check if player can afford
        if ui.get_player_money(player) < cost:
            return {'success': False, 'message': f'Cannot afford to rent {quantity} {worker_type_name}. Cost: ${cost}, have: ${ui.get_player_money(player)}'}
        
        # Rent the workers using UI method
        try:
            ui.rent_workers_for_player(player, worker_type, quantity, current_year)
        except ValueError as e:
            return {'success': False, 'message': str(e)}
        
        # Deduct marketplace supply
        marketplace.deduct_worker_supply(worker_type_name, quantity)
        
        # Deduct player money
        ui.deduct_player_money(player, cost)
        
        # Check supply AFTER rental
        supply_after = marketplace.get_worker_supply(worker_type_name)
        
        return {
            'success': True,
            'message': f'Rented {quantity} {worker_type_name} for ${cost}',
            'worker_type_name': worker_type_name,
            'quantity': quantity,
            'cost': cost,
            'remaining_money': ui.get_player_money(player),
            'supply_before': supply_before,
            'supply_after': supply_after
        }
    except Exception as e:
        print(f"Error renting workers: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': str(e)}


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
    """Start game on server - creates game for all connected players"""
    try:
        # Use current game settings
        player_names = game_settings.get('player_names', ['Player1', 'Player2'])
        map_width = game_settings.get('map_width', 100)
        map_height = game_settings.get('map_height', 75)
        starting_money = game_settings.get('starting_money', 50000)
        worker_replenishment_rate = game_settings.get('worker_replenishment_rate', 1.0)
        
        # Get all currently connected clients
        num_players = len(connected_clients)
        if num_players < 2:
            await sio.emit('error', {'message': 'Need at least 2 players connected to start game'}, room=sid)
            return
        
        # Limit player names to number of connected clients
        actual_player_names = player_names[:num_players]
        # Pad with default names if needed
        while len(actual_player_names) < num_players:
            actual_player_names.append(f"Player{len(actual_player_names) + 1}")
        
        # Create game via Operations (single source of truth)
        ai_player_indices = (data or {}).get('ai_player_indices') or []
        ui = Operations(actual_player_names, map_width, map_height, starting_money, worker_replenishment_rate, ai_player_indices=ai_player_indices)
        game = ui.get_game()
        
        # Generate session ID
        import uuid
        session_id = str(uuid.uuid4())
        
        # Store session - assign all connected clients to player slots
        players_dict = {}
        for i, client_sid in enumerate(list(connected_clients)):
            players_dict[client_sid] = i
        
        game_sessions[session_id] = {
            'game': game,
            'ui': ui,
            'players': players_dict,
            'current_turn': 0,  # Keep for compatibility, but not used
            'player_names': actual_player_names,
            'end_year_ready': set(),  # Track which players have pressed "end year"
            'creator_sid': sid,
        }
        
        # Serialize game state once for all clients
        try:
            serialized_game_state = serialize_game_state(game)
            print(f"Game state serialized successfully: {len(serialized_game_state.get('game_data', ''))} bytes")
        except Exception as e:
            print(f"Error serializing game state: {e}")
            import traceback
            traceback.print_exc()
            # Notify all connected clients of the error
            error_msg = f'Failed to serialize game state: {str(e)}'
            for client_sid in connected_clients:
                await sio.emit('error', {'message': error_msg}, room=client_sid)
            return
        
        # Add all connected clients to the session room and notify them
        for client_sid, player_id in players_dict.items():
            await sio.enter_room(client_sid, session_id)
            
            # Send game created event to each client with their assigned player info
            try:
                # Split the data to avoid potential size issues
                # Send game_state separately if it's too large
                game_data_size = len(serialized_game_state.get('game_data', ''))
                print(f"Sending game_created to client {client_sid[:8]}... (game_data size: {game_data_size} chars)")
                
                emit_data = {
                    'session_id': session_id,
                    'player_id': player_id,
                    'player_name': actual_player_names[player_id],
                    'game_state': serialized_game_state,
                    'all_players': [{'player_id': pid, 'player_name': actual_player_names[pid], 'connected': True} 
                                   for pid in range(len(players_dict))]
                }
                
                await sio.emit('game_created', emit_data, room=client_sid)
                print(f"Successfully sent game_created to client {client_sid[:8]}")
            except Exception as e:
                print(f"Error sending game_created to client {client_sid}: {e}")
                import traceback
                traceback.print_exc()
                # Try to send error to client
                try:
                    await sio.emit('error', {'message': f'Failed to send game data: {str(e)}'}, room=client_sid)
                except:
                    pass
        
        print(f"Game started: {session_id} with {len(players_dict)} connected players")
        
    except Exception as e:
        print(f"Error starting game: {e}")
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
    
    print(f"Starting TransitGame server on port {port}")
    print("Server will accept connections from clients")
    print(f"Server listening on all interfaces (0.0.0.0:{port})")
    print(f"Connect from other devices using: http://<server_ip>:{port}")
    
    web.run_app(app, host='0.0.0.0', port=port)

