package main

import (
	"log"
	"net"
	"time"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
			time.Sleep(time.Second)
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
