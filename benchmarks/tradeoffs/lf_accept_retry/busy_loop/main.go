package main

import (
	"log"
	"net"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
