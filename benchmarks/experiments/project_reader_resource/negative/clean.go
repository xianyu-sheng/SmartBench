package negative

func prometheusStyle(httpClient *Client, req *Request) error {
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return decode(resp.Body)
}

func kubernetesStyle(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return parseTemplate(file)
}

func ginStyle(fd int) error {
	file := os.NewFile(uintptr(fd), "listener")
	defer file.Close()
	listener, err := net.FileListener(file)
	if err != nil {
		return err
	}
	defer listener.Close()
	return serve(listener)
}

func terraformStyle(req *Request) error {
	resp, err := httpclient.New().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return readAll(resp.Body)
}
