use device_driver::{
    AsyncBufferInterface, AsyncRegisterInterface, BufferInterfaceBase, FieldsetMetadata,
    RegisterInterfaceBase,
};
use embedded_hal_async::i2c::I2c;

use crate::{FUSB302B_I2C_ADDRESS, FusbError};

// These are software transaction-buffer limits, not the physical FIFO sizes.
// Each I2C write starts with one register-address byte.
const ADDRESS_LEN: usize = 1;
const MAX_REGISTER_WRITE_LEN: usize = 4;
const MAX_FIFO_WRITE_LEN: usize = 63;
const REGISTER_WRITE_BUFFER_LEN: usize = ADDRESS_LEN + MAX_REGISTER_WRITE_LEN;
const FIFO_WRITE_BUFFER_LEN: usize = ADDRESS_LEN + MAX_FIFO_WRITE_LEN;

/// I2C transport for the generated register and FIFO API at the default address.
pub struct DeviceInterface<I2CBus> {
    i2c: I2CBus,
}

impl<I> DeviceInterface<I> {
    /// Wrap an async embedded-hal I2C bus.
    pub fn new(i2c: I) -> Self {
        Self { i2c }
    }
}

impl<I, E> RegisterInterfaceBase for DeviceInterface<I>
where
    I: I2c<Error = E>,
    E: core::fmt::Debug,
{
    type AddressType = u8;
    type Error = FusbError<E>;
}

impl<I, E> AsyncRegisterInterface for DeviceInterface<I>
where
    I: I2c<Error = E>,
    E: core::fmt::Debug,
{
    async fn read_register(
        &mut self,
        address: u8,
        data: &mut [u8],
        _metadata: &FieldsetMetadata,
    ) -> Result<(), Self::Error> {
        self.i2c
            .write_read(FUSB302B_I2C_ADDRESS, &[address], data)
            .await
            .map_err(FusbError::I2c)
    }

    async fn write_register(
        &mut self,
        address: u8,
        data: &mut [u8],
        _metadata: &FieldsetMetadata,
    ) -> Result<(), Self::Error> {
        let mut buffer = [0u8; REGISTER_WRITE_BUFFER_LEN];
        if (ADDRESS_LEN + data.len()) > buffer.len() {
            return Err(FusbError::LenExceedsBuffer);
        }
        buffer[0] = address;
        buffer[ADDRESS_LEN..ADDRESS_LEN + data.len()].copy_from_slice(data);
        self.i2c
            .write(FUSB302B_I2C_ADDRESS, &buffer[..ADDRESS_LEN + data.len()])
            .await
            .map_err(FusbError::I2c)
    }
}

impl<I, E> BufferInterfaceBase for DeviceInterface<I>
where
    I: I2c<Error = E>,
    E: core::fmt::Debug,
{
    type AddressType = u8;
    type Error = FusbError<E>;
}

impl<I, E> AsyncBufferInterface for DeviceInterface<I>
where
    I: I2c<Error = E>,
    E: core::fmt::Debug,
{
    async fn write(&mut self, address: u8, buf: &[u8]) -> Result<usize, Self::Error> {
        let mut buffer = [0u8; FIFO_WRITE_BUFFER_LEN];
        if (ADDRESS_LEN + buf.len()) > buffer.len() {
            return Err(FusbError::LenExceedsBuffer);
        }
        buffer[0] = address;
        buffer[ADDRESS_LEN..ADDRESS_LEN + buf.len()].copy_from_slice(buf);
        self.i2c
            .write(FUSB302B_I2C_ADDRESS, &buffer[..ADDRESS_LEN + buf.len()])
            .await
            .map_err(FusbError::I2c)?;
        Ok(buf.len())
    }

    async fn read(&mut self, address: u8, buf: &mut [u8]) -> Result<usize, Self::Error> {
        self.i2c
            .write_read(FUSB302B_I2C_ADDRESS, &[address], buf)
            .await
            .map_err(FusbError::I2c)?;
        Ok(buf.len())
    }

    async fn flush(&mut self, _address: u8) -> Result<(), Self::Error> {
        Ok(())
    }
}
