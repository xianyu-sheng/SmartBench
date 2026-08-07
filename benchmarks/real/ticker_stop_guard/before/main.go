package monitor

import "time"

func startMonitor(stop <-chan struct{}) {
	ticker := time.NewTicker(time.Second)
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			check()
		}
	}
}
