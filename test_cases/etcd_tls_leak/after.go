// Case 5: etcd TLS listener unbounded goroutine creation - FIXED
// Source: CVE-2026-73500, https://github.com/advisories/GHSA-6vch-q96h-7gc3
// Pattern: Set handshake deadline to prevent goroutine leak
// Fix: SetDeadline before Handshake, with connection limit

package main

import (
	"crypto/tls"
	"log"
	"net"
	"sync"
	"time"
)

// tlsListener wraps a TLS listener for etcd
type tlsListener struct {
	listener         net.Listener
	connChan         chan net.Conn
	config           *tls.Config
	handshakeLimiter chan struct{} // Limit concurrent handshakes
	wg               sync.WaitGroup
}

const (
	handshakeTimeout     = 10 * time.Second
	maxConcurrentHandshakes = 100
)

// NewTLSListener creates a new TLS listener
// FIXED: Added handshake timeout and concurrency limit
func NewTLSListener(addr string, config *tls.Config) (*tlsListener, error) {
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}

	tl := &tlsListener{
		listener:         listener,
		connChan:         make(chan net.Conn, 100),
		config:           config,
		handshakeLimiter: make(chan struct{}, maxConcurrentHandshakes),
	}

	go tl.acceptLoop()

	return tl, nil
}

// acceptLoop accepts incoming connections
// FIXED: Added handshake timeout and concurrency control
func (l *tlsListener) acceptLoop() {
	for {
		conn, err := l.listener.Accept()
		if err != nil {
			log.Printf("Accept error: %v", err)
			return
		}

		// FIX 1: Limit concurrent handshakes to prevent resource exhaustion
		select {
		case l.handshakeLimiter <- struct{}{}:
			// Got a slot, proceed
		default:
			// Too many concurrent handshakes, reject connection
			log.Printf("Handshake limit reached, rejecting connection")
			conn.Close()
			continue
		}

		l.wg.Add(1)
		go func(rawConn net.Conn) {
			defer l.wg.Done()
			defer func() { <-l.handshakeLimiter }() // Release slot

			// Wrap raw connection in TLS
			tlsConn := tls.Server(rawConn, l.config)

			// FIX 2: Set deadline BEFORE handshake
			// This ensures handshake cannot block forever
			deadline := time.Now().Add(handshakeTimeout)
			if err := tlsConn.SetDeadline(deadline); err != nil {
				log.Printf("SetDeadline error: %v", err)
				tlsConn.Close()
				return
			}

			// Perform handshake with timeout protection
			err := tlsConn.Handshake()
			if err != nil {
				log.Printf("Handshake error: %v", err)
				tlsConn.Close()
				return
			}

			// FIX 3: Clear deadline after successful handshake
			// Allow normal read/write operations without timeout
			if err := tlsConn.SetDeadline(time.Time{}); err != nil {
				log.Printf("Clear deadline error: %v", err)
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

// Close closes the listener and waits for all handshakes to complete
func (l *tlsListener) Close() error {
	err := l.listener.Close()
	close(l.connChan)

	// Wait for all handshake goroutines to finish
	l.wg.Wait()

	return err
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
MITIGATION:
1. SetDeadline before Handshake prevents indefinite blocking
2. Handshake timeout (10s) ensures goroutine will exit
3. Concurrency limiter prevents unbounded goroutine creation
4. WaitGroup ensures clean shutdown

Attack now fails:
- Connections without ClientHello timeout after 10s
- Max 100 concurrent handshakes limits resource usage
- System remains stable under attack
*/
