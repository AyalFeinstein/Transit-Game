# How to Start the GoCommuter Server

## Quick Start

1. **Open a terminal** in the project directory

2. **Start the server:**
   ```bash
   python server.py
   ```
   
   Or specify a custom port:
   ```bash
   python server.py 3000
   ```

3. **You should see:**
   ```
   Starting GoCommuter server on port 8080
   Server will accept connections from clients
   ```

4. **Keep this terminal open** - the server must stay running for clients to connect

## Default Settings

- **Default Port:** 8080
- **Server URL:** http://localhost:8080

## For Remote Connections

If you want to connect from another computer:

1. **Find your IP address:**
   - Linux/Mac: `hostname -I` or `ifconfig`
   - Windows: `ipconfig`

2. **Start server with your IP:**
   ```bash
   python server.py
   ```
   (The server will listen on all interfaces by default)

3. **Clients connect using:**
   ```
   http://YOUR_IP_ADDRESS:8080
   ```
   (Replace YOUR_IP_ADDRESS with the actual IP)

## Troubleshooting

### "Address already in use"
- Another server is already running on that port
- Kill the existing process or use a different port:
  ```bash
  python server.py 3000
  ```

### "Connection refused" (from client)
- Server is not running
- Start the server first before connecting clients

### "One or more namespaces failed to connect"
- Server is not running or not accessible
- Check server URL is correct
- Make sure server is running in a separate terminal
- Check firewall settings if connecting remotely

## Testing the Server

You can test if the server is running by visiting in a browser:
```
http://localhost:8080
```

You should see a JSON response with server status.

## Stopping the Server

Press `Ctrl+C` in the server terminal to stop it.

