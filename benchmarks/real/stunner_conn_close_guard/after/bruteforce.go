package bruteforce

import (
	"net"

	"github.com/firefart/stunner/internal"
)

func testPassword(addr string, password string) error {
	remote, err := internal.Connect(addr)
	if err != nil {
		return err
	}
	defer remote.Close()
	response, err := sendAndReceive(remote, password)
	if err != nil {
		return err
	}
	return validate(response)
}
