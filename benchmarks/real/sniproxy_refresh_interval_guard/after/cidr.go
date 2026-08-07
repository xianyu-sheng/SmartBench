package acl

import (
	"fmt"
	"os"
	"time"

	"github.com/knadh/koanf"
	"github.com/rs/zerolog"
)

type cidr struct {
	Path            string
	RefreshInterval time.Duration
	logger          *zerolog.Logger
	stopCh          chan struct{}
	doneCh          chan struct{}
}

func (d *cidr) loadCIDRCSVWorker(path string, interval time.Duration) {
	defer close(d.doneCh)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-d.stopCh:
			return
		case <-ticker.C:
			_ = d.LoadCIDRCSV(path)
		}
	}
}

func (d *cidr) LoadCIDRCSV(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	return nil
}

func (d *cidr) ConfigAndStart(logger *zerolog.Logger, c *koanf.Koanf) error {
	c = c.Cut(fmt.Sprintf("acl.%s", d.Name()))
	d.logger = logger
	d.Path = c.String("path")
	d.RefreshInterval = c.Duration("refresh_interval")
	if d.RefreshInterval <= 0 {
		return fmt.Errorf("acl.cidr.refresh_interval must be a positive duration, got %v", d.RefreshInterval)
	}
	d.stopCh = make(chan struct{})
	d.doneCh = make(chan struct{})
	go d.loadCIDRCSVWorker(d.Path, d.RefreshInterval)
	return nil
}

func (d *cidr) Name() string { return "cidr" }

func (d *cidr) Priority() uint { return 0 }

func (d *cidr) Stop() {
	close(d.stopCh)
	<-d.doneCh
}
