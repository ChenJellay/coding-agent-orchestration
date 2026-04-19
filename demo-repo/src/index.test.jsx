import React from 'react';
import { render } from '@testing-library/react';
import '@testing-library/jest-dom';
import index from './index';

// Mock external dependencies
jest.mock('./components/header', () => {
  const Header = () => <div data-testid="header">Header Content</div>;
  return Header;
});

describe('index', () => {
  test('renders rubber ducky emoji centered on the page', () => {
    const { container } = render(<index />);

    // Assert Header is rendered
    expect(container.getByTestId('header')).toBeInTheDocument();

    // Assert rubber ducky emoji is rendered
    const duckyElement = container.getByText('🦆');
    expect(duckyElement).toBeInTheDocument();

    // Assert centering (checking for common centering styles or classes)
    // Check if the parent of the ducky has the 'centered' class
    const parentElement = duckyElement.parentElement;
    expect(parentElement).toHaveClass('centered');
  });
});
