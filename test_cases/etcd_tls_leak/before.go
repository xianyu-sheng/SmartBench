// Case 5: etcd TLS listener unbounded goroutine creation
// Source: CVE-2026-73500, https://github.com/advisories/GHSA-6vch-q96h-7gc3
// Pattern: Accept loop spawns goroutines without handshake timeout
// Impact: DoS via file descriptor and memory exhaustion

package main

import (
	"crypto/tls"
	"log"
	"net"
	"time"
)

// tlsListener wraps a TLS listener for etcd
type tlsListener struct {
	listener net.Listener
	connChan chan net.Conn
	config   *tls.Config
}

// NewTLSListener creates a new TLS listener
// BUG: No handshake timeout leads to unbounded goroutine creation
func NewTLSListener(addr string, config *tls.Config) (*tlsListener, error) {
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}

	tl := &tlsListener{
		listener: listener,
		connChan: make(chan net.Conn, 100),
		config:   config,
	}

	go tl.acceptLoop()

	return tl, nil
}

// acceptLoop accepts incoming connections
// BUG: Each connection spawns a goroutine that can block forever
func (l *tlsListener) acceptLoop() {
	for {
		conn, err := l.listener.Accept()
		if err != nil {
			log.Printf("Accept error: %v", err)
			return
		}

		// BUG: Spawn unbounded goroutines without any limit
		go func(rawConn net.Conn) {
			// Wrap raw connection in TLS
			tlsConn := tls.Server(rawConn, l.config)

			// CRITICAL BUG: No deadline set before handshake
			// If client never sends ClientHello, this blocks forever
			// Each such connection leaks:
			// - 1 goroutine (~10KB stack)
			// - 1 file descriptor
			// - Connection buffers
			err := tlsConn.Handshake()
			if err != nil {
				log.Printf("Handshake error: %v", err)
				tlsConn.Close()
				return
			}

			// Send accepted connection to handler
			select {
			case l.connChan <- tlsConn:
			case <-time.After(1 * time.Second):
				// Connection queue full
				tlsConn.Close()
			}
		}(conn)
	}
}

// Accept returns the next accepted connection
func (l *tlsListener) Accept() (net.Conn, error) {
	conn, ok := <-l.connChan
	if !ok {
		return nil, net.ErrClosed
	}
	return conn, nil
}

// Close closes the listener
func (l *tlsListener) Close() error {
	close(l.connChan)
	return l.listener.Close()
}

func main() {
	config := &tls.Config{
		// TLS config would be here
	}

	listener, err := NewTLSListener(":2379", config)
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()

	// Handle connections
	for {
		conn, err := listener.Accept()
		if err != nil {
			log.Printf("Accept error: %v", err)
			break
		}

		// Handle connection
		go handleConnection(conn)
	}
}

func handleConnection(conn net.Conn) {
	defer conn.Close()
	// Connection handling logic
}

/*
ATTACK SCENARIO:
An attacker can open thousands of TCP connections and never send ClientHello:

  for i in {1..10000}; do
    nc etcd-server 2379 &
  done

Each connection spawns a goroutine blocked in tlsConn.Handshake() forever.
Result: File descriptor exhaustion, memory exhaustion, system crash.
*/
