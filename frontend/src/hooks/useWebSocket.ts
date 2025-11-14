import { useEffect, useRef, useState, useCallback } from 'react';
import type {
  WebSocketMessage,
  SyncProgressMessage,
  LogEntryMessage,
  ScheduleRunMessage,
  ErrorMessage,
} from '../types/api';

interface UseWebSocketOptions {
  url?: string;
  autoConnect?: boolean;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
  onOpen?: () => void;
  onClose?: () => void;
  onSocketError?: (error: Event) => void;
  onMessage?: (message: WebSocketMessage) => void;
  onSyncProgress?: (service: string, data: SyncProgressMessage) => void;
  onLogEntry?: (service: string, data: LogEntryMessage) => void;
  onScheduleRun?: (service: string, data: ScheduleRunMessage) => void;
  onServiceError?: (service: string, data: ErrorMessage) => void;
  onStatusUpdate?: (service: string, data: Record<string, unknown>) => void;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  isConnecting: boolean;
  error: string | null;
  connect: () => void;
  disconnect: () => void;
  send: (message: unknown) => void;
  subscribe: (service: string) => void;
  unsubscribe: (service: string) => void;
}

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketReturn {
  const {
    url = `ws://${window.location.host}/api/ws`,
    autoConnect = true,
    reconnectInterval = 5000,
    maxReconnectAttempts = 10,
    onOpen,
    onClose,
    onSocketError,
    onMessage,
    onSyncProgress,
    onLogEntry,
    onScheduleRun,
    onServiceError,
    onStatusUpdate,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearReconnectTimeout = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const clearPingInterval = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
  }, []);

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message: WebSocketMessage = JSON.parse(event.data);

      // Call general message handler
      onMessage?.(message);

      // Call specific handlers based on message type
      switch (message.type) {
        case 'sync_progress':
          if (message.service && message.data) {
            onSyncProgress?.(message.service, message.data as SyncProgressMessage);
          }
          break;

        case 'log_entry':
          if (message.service && message.data) {
            onLogEntry?.(message.service, message.data as LogEntryMessage);
          }
          break;

        case 'schedule_run':
          if (message.service && message.data) {
            onScheduleRun?.(message.service, message.data as ScheduleRunMessage);
          }
          break;

        case 'error':
          if (message.service && message.data) {
            onServiceError?.(message.service, message.data as ErrorMessage);
          }
          break;

        case 'status_update':
          if (message.service && message.data) {
            onStatusUpdate?.(message.service, message.data as Record<string, unknown>);
          }
          break;

        case 'connection':
          console.log('WebSocket connection established:', message.message);
          break;

        case 'pong':
          // Pong received, connection is alive
          break;

        default:
          console.log('Unknown message type:', message.type);
      }
    } catch (err) {
      console.error('Error parsing WebSocket message:', err);
      setError('Failed to parse message');
    }
  }, [onMessage, onSyncProgress, onLogEntry, onScheduleRun, onServiceError, onStatusUpdate]);

  const startPing = useCallback(() => {
    clearPingInterval();
    pingIntervalRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000); // Ping every 30 seconds
  }, [clearPingInterval]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN || isConnecting) {
      return;
    }

    setIsConnecting(true);
    setError(null);

    try {
      const ws = new WebSocket(url);

      ws.onopen = () => {
        console.log('WebSocket connected');
        setIsConnected(true);
        setIsConnecting(false);
        setError(null);
        reconnectAttemptsRef.current = 0;
        clearReconnectTimeout();
        startPing();
        onOpen?.();
      };

      ws.onclose = () => {
        if (import.meta.env.DEV) {
          console.log('WebSocket disconnected');
        }
        setIsConnected(false);
        setIsConnecting(false);
        clearPingInterval();
        onClose?.();

        // Attempt to reconnect
        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          reconnectAttemptsRef.current++;
          if (import.meta.env.DEV) {
            console.log(
              `WebSocket reconnecting... (attempt ${reconnectAttemptsRef.current}/${maxReconnectAttempts})`
            );
          }
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, reconnectInterval);
        } else {
          setError('WebSocket unavailable');
        }
      };

      ws.onerror = (event) => {
        // Only log WebSocket errors in development to avoid console noise
        if (import.meta.env.DEV) {
          console.warn('WebSocket connection failed (real-time updates unavailable)');
        }
        setError('WebSocket connection error');
        onSocketError?.(event);
      };

      ws.onmessage = handleMessage;

      wsRef.current = ws;
    } catch (err) {
      console.error('Failed to create WebSocket connection:', err);
      setError('Failed to create connection');
      setIsConnecting(false);
    }
  }, [
    url,
    isConnecting,
    maxReconnectAttempts,
    reconnectInterval,
    onOpen,
    onClose,
    onSocketError,
    handleMessage,
    clearReconnectTimeout,
    startPing,
    clearPingInterval,
  ]);

  const disconnect = useCallback(() => {
    clearReconnectTimeout();
    clearPingInterval();
    reconnectAttemptsRef.current = maxReconnectAttempts; // Prevent reconnection

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setIsConnected(false);
    setIsConnecting(false);
  }, [clearReconnectTimeout, clearPingInterval, maxReconnectAttempts]);

  const send = useCallback((message: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket is not connected');
    }
  }, []);

  const subscribe = useCallback((service: string) => {
    send({ type: 'subscribe', service });
  }, [send]);

  const unsubscribe = useCallback((service: string) => {
    send({ type: 'unsubscribe', service });
  }, [send]);

  // Auto-connect on mount if enabled
  useEffect(() => {
    if (!autoConnect) {
      return;
    }

    connect();

    // Cleanup on unmount
    return () => {
      disconnect();
    };
    // Intentionally omit connect/disconnect from deps to avoid reconnect loops
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConnect]);

  return {
    isConnected,
    isConnecting,
    error,
    connect,
    disconnect,
    send,
    subscribe,
    unsubscribe,
  };
}
