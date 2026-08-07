package store

import "sync"

type Store struct {
	mu   sync.Mutex
	data map[string]string
}

func (s *Store) Get(key string) (string, error) {
	s.mu.Lock()
	v, ok := s.data[key]
	if !ok {
		return "", errNotFound
	}
	s.mu.Unlock()
	return v, nil
}
